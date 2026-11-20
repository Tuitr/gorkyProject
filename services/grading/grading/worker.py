"""Consume grading events and acknowledge them after PostgreSQL commits."""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta

from pydantic import TypeAdapter
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from contracts.v1.references.reference import ReferenceRequest, ReferenceResult
from grading.assignment import AssignmentTask, AssignmentTextReader, DocxTextReader, readAssignment
from grading.inbox import SavedJob, saveEvent
from grading.intake import AssignmentPublishedInput, AttemptSubmittedInput, IncomingDocRef
from grading.jobs import JobLease, JobQueue
from grading.references import ReferenceClient
from grading.stream import StreamConsumer, StreamDelivery
from grading.submission import SolutionStep, SubmissionReading, readSubmissionDetails
from grading.tables import AssignmentJob

parsedTasksAdapter: TypeAdapter[tuple[AssignmentTask, ...]] = TypeAdapter(tuple[AssignmentTask, ...])
submissionReadingAdapter = TypeAdapter(SubmissionReading)
referencesAdapter: TypeAdapter[dict[str, ReferenceResult]] = TypeAdapter(dict[str, ReferenceResult])
deadlinesAdapter: TypeAdapter[dict[str, datetime]] = TypeAdapter(dict[str, datetime])
SUBMISSION_PARSE_VERSION = 3
ASSIGNMENT_RECHECK_DELAY = timedelta(seconds=5)


@dataclass(frozen=True)
class PreparedCheck:
    reading: SubmissionReading
    references: dict[str, ReferenceResult]


class GradingWorker:
    def __init__(
        self, consumer: StreamConsumer, engine: Engine, *, pendingIdleMs: int = 60_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.consumer = consumer
        self.engine = engine
        self.pendingIdleMs = pendingIdleMs
        self.jobs = JobQueue(engine, clock=clock)
        self.now = clock or (lambda: datetime.now(UTC))

    def runBatch(self, *, count: int = 10, blockMs: int | None = None) -> list[SavedJob]:
        """Persist pending and fresh deliveries. A zero block uses a one-second poll."""
        pending = self.consumer.readPending(minIdleMs=self.pendingIdleMs, count=count)
        saved = [self.persistDelivery(delivery) for delivery in pending]
        remaining = count - len(saved)
        if remaining:
            # Return periodically so the next pass can reclaim deliveries that have just become idle.
            wait = None if saved else (1_000 if blockMs == 0 else blockMs)
            saved.extend(
                self.persistDelivery(delivery) for delivery in self.consumer.readBatch(count=remaining, blockMs=wait)
            )
        return saved

    def persistDelivery(self, delivery: StreamDelivery) -> SavedJob:
        with Session(self.engine) as session, session.begin():
            job = saveEvent(session, delivery.event)
        self.consumer.acknowledge(delivery.messageId)
        return job

    def parseAssignment(
        self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes], *,
        reader: AssignmentTextReader = DocxTextReader(),
    ) -> tuple[AssignmentTask, ...]:
        """Parse a leased assignment and checkpoint the tasks for reference lookup."""
        if isinstance(lease.key, str):
            raise ValueError("expected an assignment preparation job")
        if lease.completedStage in ("assignment_parsed", "requesting_references", "references_requested") and lease.stageData is not None:
            return parsedTasksAdapter.validate_python(lease.stageData["tasks"])
        if lease.completedStage is not None:
            raise ValueError("unexpected checkpoint for assignment parsing")
        published = AssignmentPublishedInput.model_validate(lease.payload)
        tasks = readAssignment(published, loadDocument(published.docRef), reader=reader)
        if not self.jobs.checkpoint(lease, "assignment_parsed", {"tasks": [asdict(task) for task in tasks]}):
            raise RuntimeError("assignment lease expired before the parse could be saved")
        return tasks

    def prepareAssignment(
        self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes], *,
        requestReference: Callable[[ReferenceRequest], ReferenceResult] | None = None,
        getReference: Callable[[str], ReferenceResult] | None = None,
    ) -> dict[str, ReferenceResult]:
        """Register tasks once per published revision and retain partial progress on restart."""
        if isinstance(lease.key, str):
            raise ValueError("expected an assignment preparation job")
        tasks = self.parseAssignment(lease, loadDocument)
        saved = lease.stageData or {}
        references = referencesAdapter.validate_python(saved.get("references", {}))
        deadlines = deadlinesAdapter.validate_python(saved.get("nextPollAt", {}))
        data: dict[str, object] = {"tasks": [asdict(task) for task in tasks]}
        for task in tasks:
            previous = references.get(task.taskId)
            if previous is None:
                if requestReference is None:
                    requestReference = ReferenceClient.fromEnv().request
                result = requestReference(ReferenceRequest(
                    assignmentId=lease.key[0], assignmentRev=lease.key[1], taskId=task.taskId,
                    statement=task.statement, constraints=task.constraints,
                ))
            else:
                if previous.state != "pending" or (task.taskId in deadlines and deadlines[task.taskId] > self.now()):
                    continue
                if getReference is None:
                    getReference = ReferenceClient.fromEnv().get
                result = getReference(previous.referenceId)
                if result.referenceId != previous.referenceId:
                    raise ValueError("reference registry returned a different referenceId on GET")
            references[task.taskId] = result
            if result.state == "pending":
                deadlines[task.taskId] = self.now() + timedelta(seconds=max(1, result.retryAfterS))
            else:
                deadlines.pop(task.taskId, None)
            data["nextPollAt"] = deadlinesAdapter.dump_python(deadlines, mode="json")
            data["references"] = referencesAdapter.dump_python(references, mode="json")
            if not self.jobs.checkpoint(lease, "requesting_references", data):
                raise RuntimeError("assignment lease expired before the reference could be saved")

        data["references"] = referencesAdapter.dump_python(references, mode="json")
        data["nextPollAt"] = deadlinesAdapter.dump_python(deadlines, mode="json")
        if deadlines:
            savedProgress = self.jobs.checkpoint(lease, "references_requested", data) and self.jobs.reschedule(
                lease, "waiting_reference", min(deadlines.values()),
            )
        else:
            ready = all(result.state == "ready" for result in references.values())
            savedProgress = self.jobs.finish(lease, "ready" if ready else "needs_review", "references_requested", data)
        if not savedProgress:
            raise RuntimeError("assignment lease expired before preparation was saved")
        return references

    def prepareCheck(self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes]) -> PreparedCheck | None:
        """Use the reference bindings of this revision or release the check until they exist."""
        if not isinstance(lease.key, str):
            raise ValueError("expected a submission check job")
        submitted = AttemptSubmittedInput.model_validate(lease.payload)
        with Session(self.engine) as session:
            job = session.get(AssignmentJob, (submitted.assignmentId, submitted.assignmentRev))
            if job is not None and job.state in ("ready", "needs_review") and job.completedStage == "references_requested":
                if job.stageData is None:
                    raise ValueError("missing references for prepared assignment")
                references = referencesAdapter.validate_python(job.stageData["references"])
            else:
                references = None
        if references is None:
            if not self.jobs.waitForReference(lease, ASSIGNMENT_RECHECK_DELAY):
                raise RuntimeError("check lease expired before it could wait for the assignment")
            return None
        return PreparedCheck(self.parseSubmissionDetails(lease, loadDocument), references)

    def parseSubmission(self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes]) -> tuple[SolutionStep, ...]:
        return self.parseSubmissionDetails(lease, loadDocument).steps

    def parseSubmissionDetails(self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes]) -> SubmissionReading:
        if not isinstance(lease.key, str):
            raise ValueError("expected a submission check job")
        if lease.completedStage == "submission_parsed" and lease.stageData is not None:
            version = lease.stageData.get("parserVersion", 1)
            if version == SUBMISSION_PARSE_VERSION:
                return submissionReadingAdapter.validate_python(lease.stageData)
            if version not in (1, 2):
                raise ValueError("unsupported submission parser checkpoint version")
        elif lease.completedStage is not None:
            raise ValueError("unexpected checkpoint for submission parsing")
        submitted = AttemptSubmittedInput.model_validate(lease.payload)
        with Session(self.engine) as session:
            assignment = session.get(AssignmentJob, (submitted.assignmentId, submitted.assignmentRev))
            if assignment is None or assignment.stageData is None or "tasks" not in assignment.stageData:
                raise ValueError("parsed tasks are unavailable for the submitted assignment revision")
            tasks = parsedTasksAdapter.validate_python(assignment.stageData["tasks"])
        reading = readSubmissionDetails(submitted, loadDocument(submitted.docRef), tasks)
        data = {"parserVersion": SUBMISSION_PARSE_VERSION, **submissionReadingAdapter.dump_python(reading, mode="json")}
        if not self.jobs.checkpoint(lease, "submission_parsed", data):
            raise RuntimeError("check lease expired before the submission parse could be saved")
        return reading
