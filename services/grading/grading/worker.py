"""Consume grading events and acknowledge them after PostgreSQL commits."""

from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta

from pydantic import TypeAdapter
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from contracts.v1.grading.checkresult import CheckOutcome, PublishedCheck
from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from contracts.v1.references.reference import ReferenceRequest, ReferenceResult
from grading.assignment import AssignmentTask, AssignmentTextReader, PhotoClarification, readAssignment
from grading.feedback import composeOverallReview
from grading.inbox import SavedJob, saveEvent
from grading.intake import AssignmentPublishedInput, AttemptSubmittedInput, IncomingDocRef, TaskPoints
from grading.jobs import JobLease, JobQueue
from grading.markdownStorage import MarkdownStorage
from grading.photoOcr import TranscribedLine, transcribePhoto
from grading.references import ReferenceClient, ReferenceTerminalError, ReferenceUnavailableError
from grading.resultArtifacts import parsedTasksAdapter, publishResultArtifact, submissionReadingAdapter
from grading.resultArtifacts import referencesAdapter as referencesAdapter
from grading.results import ResultStore
from grading.review import PROMPT_VERSION, reviewTasks, sendReview
from grading.stream import StreamConsumer, StreamDelivery
from grading.submission import SolutionStep, SubmissionReading, readPhotoPages, readSubmissionDetails
from grading.tables import AssignmentJob
from shared.submissionmedia import photoMediaType

deadlinesAdapter: TypeAdapter[dict[str, datetime]] = TypeAdapter(dict[str, datetime])
referenceAttemptsAdapter: TypeAdapter[dict[str, int]] = TypeAdapter(dict[str, int])
referenceErrorsAdapter: TypeAdapter[dict[str, dict[str, object]]] = TypeAdapter(dict[str, dict[str, object]])
taskPointsAdapter = TypeAdapter(list[TaskPoints])
photoPagesAdapter = TypeAdapter(list[tuple[TranscribedLine, ...]])
SUBMISSION_PARSE_VERSION = 5
ASSIGNMENT_RECHECK_DELAY = timedelta(seconds=5)
REFERENCE_RETRY_LIMIT = 3


@dataclass(frozen=True)
class PreparedCheck:
    reading: SubmissionReading
    references: dict[str, ReferenceResult]
    referenceErrors: dict[str, dict[str, object]] = field(default_factory=dict)
    tasks: tuple[AssignmentTask, ...] = ()
    maxima: dict[str, float] = field(default_factory=dict)


class GradingWorker:
    def __init__(
        self, consumer: StreamConsumer, engine: Engine, *, pendingIdleMs: int = 60_000,
        clock: Callable[[], datetime] | None = None,
        ocr: Callable[[bytes, str], tuple[TranscribedLine, ...]] = transcribePhoto,
    ) -> None:
        self.consumer = consumer
        self.engine = engine
        self.pendingIdleMs = pendingIdleMs
        self.jobs = JobQueue(engine, clock=clock)
        self.now = clock or (lambda: datetime.now(UTC))
        self.results = ResultStore(engine, clock)
        self.ocr = ocr

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
        reader: AssignmentTextReader | None = None,
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
        try:
            tasks = self.parseAssignment(lease, loadDocument)
        except PhotoClarification as error:
            if not self.jobs.finish(lease, "needs_review", "assignment_photo_unclear", {"detail": str(error)}):
                raise RuntimeError("assignment lease expired before clarification could be saved") from error
            return {}
        saved = lease.stageData or {}
        references = referencesAdapter.validate_python(saved.get("references", {}))
        deadlines = deadlinesAdapter.validate_python(saved.get("nextPollAt", {}))
        transientAttempts = referenceAttemptsAdapter.validate_python(saved.get("referenceAttempts", {}))
        referenceErrors = referenceErrorsAdapter.validate_python(saved.get("referenceErrors", {}))
        data: dict[str, object] = {"tasks": [asdict(task) for task in tasks]}
        for task in tasks:
            previous = references.get(task.taskId)
            failure = referenceErrors.get(task.taskId)
            if failure and (failure["reasonCode"] != "reference_registry_unavailable"
                            or transientAttempts.get(task.taskId, 0) >= REFERENCE_RETRY_LIMIT):
                deadlines.pop(task.taskId, None)
                if previous is not None and previous.state == "pending":
                    failure["referenceId"] = previous.referenceId
                    del references[task.taskId]
                continue
            if previous is not None and previous.state != "pending":
                continue
            if task.taskId in deadlines and deadlines[task.taskId] > self.now():
                continue
            try:
                if previous is None:
                    if requestReference is None:
                        requestReference = ReferenceClient.fromEnv().request
                    result = requestReference(ReferenceRequest(
                        assignmentId=lease.key[0], assignmentRev=lease.key[1], taskId=task.taskId,
                        statement=task.statement, constraints=task.constraints,
                    ))
                else:
                    if getReference is None:
                        getReference = ReferenceClient.fromEnv().get
                    result = getReference(previous.referenceId)
                    if result.referenceId != previous.referenceId:
                        raise ValueError("reference registry returned a different referenceId on GET")
            except ReferenceTerminalError as error:
                referenceErrors[task.taskId] = {"reasonCode": error.reasonCode, "message": str(error)}
                if previous is not None:
                    referenceErrors[task.taskId]["referenceId"] = previous.referenceId
                    del references[task.taskId]
                deadlines.pop(task.taskId, None)
            except ReferenceUnavailableError as error:
                attempts = transientAttempts.get(task.taskId, 0) + 1
                transientAttempts[task.taskId] = attempts
                referenceErrors[task.taskId] = {
                    "reasonCode": "reference_registry_unavailable",
                    "message": str(error),
                    "attempts": attempts,
                }
                if attempts >= REFERENCE_RETRY_LIMIT:
                    deadlines.pop(task.taskId, None)
                    if previous is not None:
                        referenceErrors[task.taskId]["referenceId"] = previous.referenceId
                        del references[task.taskId]
                else:
                    deadlines[task.taskId] = self.now() + timedelta(seconds=error.retryAfterS)
            else:
                references[task.taskId] = result
                referenceErrors.pop(task.taskId, None)
                if result.state == "pending":
                    deadlines[task.taskId] = self.now() + timedelta(seconds=max(1, result.retryAfterS))
                else:
                    deadlines.pop(task.taskId, None)
            data["nextPollAt"] = deadlinesAdapter.dump_python(deadlines, mode="json")
            data["references"] = referencesAdapter.dump_python(references, mode="json")
            data["referenceAttempts"] = transientAttempts
            data["referenceErrors"] = referenceErrors
            if not self.jobs.checkpoint(lease, "requesting_references", data):
                raise RuntimeError("assignment lease expired before the reference could be saved")

        data["references"] = referencesAdapter.dump_python(references, mode="json")
        data["nextPollAt"] = deadlinesAdapter.dump_python(deadlines, mode="json")
        data["referenceAttempts"] = transientAttempts
        data["referenceErrors"] = referenceErrors
        if deadlines:
            savedProgress = self.jobs.checkpoint(lease, "references_requested", data) and self.jobs.reschedule(
                lease, "waiting_reference", min(deadlines.values()),
            )
        else:
            ready = len(references) == len(tasks) and not referenceErrors and all(
                result.state == "ready" for result in references.values()
            )
            savedProgress = self.jobs.finish(lease, "ready" if ready else "needs_review", "references_requested", data)
        if not savedProgress:
            raise RuntimeError("assignment lease expired before preparation was saved")
        return references

    def prepareCheck(self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes]) -> PreparedCheck | None:
        """Use the reference bindings of this revision or release the check until they exist."""
        if not isinstance(lease.key, str):
            raise ValueError("expected a submission check job")
        submitted = AttemptSubmittedInput.model_validate(lease.payload)
        referenceErrors: dict[str, dict[str, object]] = {}
        failed = False
        with Session(self.engine) as session:
            job = session.get(AssignmentJob, (submitted.assignmentId, submitted.assignmentRev))
            if job is not None and job.state in ("ready", "needs_review") and job.completedStage in ("references_requested", "reference_unavailable"):
                if job.stageData is None:
                    raise ValueError("missing references for prepared assignment")
                maxima = {f"t{point.taskNumber}": point.maxScore for point in
                          taskPointsAdapter.validate_python(job.payload.get("taskPoints", []))}
                references = referencesAdapter.validate_python(job.stageData["references"])
                tasks = parsedTasksAdapter.validate_python(job.stageData["tasks"])
                referenceErrors = referenceErrorsAdapter.validate_python(job.stageData.get("referenceErrors", {}))
            elif job is not None and job.completedStage == "assignment_photo_unclear":
                if not self.jobs.finish(lease, "needs_review", "assignment_photo_unclear"):
                    raise RuntimeError("check lease expired before clarification could be saved")
                return None
            elif job is not None and job.state == "failed":
                failed = True
                references = None
            else:
                references = None
        if failed:
            # The assignment revision itself could not be read: the teacher decides, nothing is waited for.
            if not self.jobs.finish(lease, "needs_review", "assignment_failed"):
                raise RuntimeError("check lease expired before the assignment failure was saved")
            return None
        if references is None:
            if not self.jobs.waitForReference(lease, ASSIGNMENT_RECHECK_DELAY):
                raise RuntimeError("check lease expired before it could wait for the assignment")
            return None
        reading = self.parseSubmissionDetails(lease, loadDocument)
        available: dict[str, ReferenceResult] = {}
        for task in reading.taskReadings:
            result = references.get(task.taskId)
            if result is not None and result.state == "ready" and task.taskId not in referenceErrors:
                available[task.taskId] = result
            elif task.taskId not in referenceErrors:
                reason = result.reasonCode if result is not None and result.state in ("unsupported", "validation_failed") else "reference_unavailable"
                referenceErrors[task.taskId] = {"reasonCode": reason}
        if referenceErrors:
            data = {
                **submissionReadingAdapter.dump_python(reading, mode="json"), "parserVersion": SUBMISSION_PARSE_VERSION,
                "references": referencesAdapter.dump_python(references, mode="json"), "referenceErrors": referenceErrors,
            }
            if (not available and reading.ocrStatus != "unreadable"
                    and not any(item.status in ("skipped", "omitted") for item in reading.taskReadings)):
                if not self.jobs.finish(lease, "needs_review", "reference_unavailable", data):
                    raise RuntimeError("check lease expired before reference failure was saved")
                return None
            if not self.jobs.checkpoint(lease, "submission_parsed", data):
                raise RuntimeError("check lease expired before partial preparation was saved")
        return PreparedCheck(reading, available, referenceErrors, tasks, maxima)

    def reviewCheck(
        self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes], *,
        transport: Callable[[ModelReviewRequest], ModelReviewResponse] = sendReview, promptVersion: str = PROMPT_VERSION,
    ) -> tuple[TaskResult, ...] | None:
        if not isinstance(lease.key, str):
            raise ValueError("expected a submission check job")
        prepared = self.prepareCheck(lease, loadDocument)
        if prepared is None:
            return None
        return reviewTasks(lease.key, prepared.tasks, prepared.reading, prepared.references, transport, promptVersion)

    def calculateCheck(
        self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes], *,
        overallReview: str | None = None, transport: Callable[[ModelReviewRequest], ModelReviewResponse] = sendReview,
    ) -> CheckOutcome | None:
        """Store the initial calculation exactly once so a retry can resume publication."""
        if not isinstance(lease.key, str):
            raise ValueError("expected a submission check job")
        existing = self.results.findDraft(lease.key, "initial")
        if existing is not None:
            return existing
        prepared = self.prepareCheck(lease, loadDocument)
        if prepared is None:
            return None
        results = reviewTasks(lease.key, prepared.tasks, prepared.reading, prepared.references, transport)
        if overallReview is None:
            overallReview = composeOverallReview(prepared.tasks, prepared.reading, results, prepared.references)
        return self.results.saveDraft(lease.key, "initial", results, overallReview, lease=lease, maxima=prepared.maxima,
                                      omittedTasks=prepared.reading.omittedTaskIds,
                                      answerScoredTasks=prepared.reading.answerScoredTaskIds(prepared.references))

    def completeCheck(
        self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes], storage: MarkdownStorage, *,
        transport: Callable[[ModelReviewRequest], ModelReviewResponse] = sendReview,
    ) -> PublishedCheck | None:
        """Resume from the stored calculation when an artifact write needs a retry."""
        draft = self.calculateCheck(lease, loadDocument, transport=transport)
        if draft is None:
            return None
        return self.publishCheckResult(draft.checkId, draft.resultRev, storage)

    def publishCheckResult(self, checkId: str, resultRev: int, storage: MarkdownStorage) -> PublishedCheck:
        return publishResultArtifact(self.results, checkId, resultRev, storage)

    def parseSubmission(self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes]) -> tuple[SolutionStep, ...]:
        return self.parseSubmissionDetails(lease, loadDocument).steps

    def parseSubmissionDetails(self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes]) -> SubmissionReading:
        if not isinstance(lease.key, str):
            raise ValueError("expected a submission check job")
        submitted = AttemptSubmittedInput.model_validate(lease.payload)
        if lease.completedStage == "submission_parsed" and lease.stageData is not None:
            version = lease.stageData.get("parserVersion", 1)
            if version in (4, SUBMISSION_PARSE_VERSION) or (version == 3 and not submitted.pageRefs):
                return submissionReadingAdapter.validate_python(lease.stageData)
            if version not in (1, 2, 3):
                raise ValueError("unsupported submission parser checkpoint version")
        elif lease.completedStage not in (None, "photo_pages_read"):
            raise ValueError("unexpected checkpoint for submission parsing")
        with Session(self.engine) as session:
            assignment = session.get(AssignmentJob, (submitted.assignmentId, submitted.assignmentRev))
            if assignment is None or assignment.stageData is None or "tasks" not in assignment.stageData:
                raise ValueError("parsed tasks are unavailable for the submitted assignment revision")
            tasks = parsedTasksAdapter.validate_python(assignment.stageData["tasks"])
        media = photoMediaType(submitted.docRef.key)
        if media is None:
            reading = readSubmissionDetails(submitted, loadDocument(submitted.docRef), tasks)
        else:
            refs = submitted.pageRefs or [submitted.docRef]
            pages = photoPagesAdapter.validate_python((lease.stageData or {}).get("ocrLines", []))
            for ref in refs[len(pages):]:
                pageMedia = photoMediaType(ref.key)
                assert pageMedia is not None
                pages.append(self.ocr(loadDocument(ref), pageMedia))
                progress = {"ocrLines": photoPagesAdapter.dump_python(pages, mode="json")}
                if not self.jobs.checkpoint(lease, "photo_pages_read", progress):
                    raise RuntimeError("check lease expired before photo progress was saved")
            reading = readPhotoPages(refs, pages, tasks)
        data = {"parserVersion": SUBMISSION_PARSE_VERSION, **submissionReadingAdapter.dump_python(reading, mode="json")}
        if not self.jobs.checkpoint(lease, "submission_parsed", data):
            raise RuntimeError("check lease expired before the submission parse could be saved")
        return reading
