"""Consume grading events and acknowledge them after PostgreSQL commits."""

from collections.abc import Callable
from dataclasses import asdict
from datetime import datetime

from pydantic import TypeAdapter
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from grading.assignment import AssignmentTask, AssignmentTextReader, DocxTextReader, readAssignment
from grading.inbox import SavedJob, saveEvent
from grading.intake import AssignmentPublishedInput, AttemptSubmittedInput, IncomingDocRef
from grading.jobs import JobLease, JobQueue
from grading.stream import StreamConsumer, StreamDelivery
from grading.submission import SolutionStep, readSubmission
from grading.tables import AssignmentJob

parsedTasksAdapter: TypeAdapter[tuple[AssignmentTask, ...]] = TypeAdapter(tuple[AssignmentTask, ...])
solutionStepsAdapter: TypeAdapter[tuple[SolutionStep, ...]] = TypeAdapter(tuple[SolutionStep, ...])
SUBMISSION_PARSE_VERSION = 2  # The original checkpoint omitted OMML, including equation-only lines.


class GradingWorker:
    def __init__(
        self, consumer: StreamConsumer, engine: Engine, *, pendingIdleMs: int = 60_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.consumer = consumer
        self.engine = engine
        self.pendingIdleMs = pendingIdleMs
        self.jobs = JobQueue(engine, clock=clock)

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
        if lease.completedStage == "assignment_parsed" and lease.stageData is not None:
            return parsedTasksAdapter.validate_python(lease.stageData["tasks"])
        if lease.completedStage is not None:
            raise ValueError("unexpected checkpoint for assignment parsing")
        published = AssignmentPublishedInput.model_validate(lease.payload)
        tasks = readAssignment(published, loadDocument(published.docRef), reader=reader)
        if not self.jobs.checkpoint(lease, "assignment_parsed", {"tasks": [asdict(task) for task in tasks]}):
            raise RuntimeError("assignment lease expired before the parse could be saved")
        return tasks

    def parseSubmission(self, lease: JobLease, loadDocument: Callable[[IncomingDocRef], bytes]) -> tuple[SolutionStep, ...]:
        if not isinstance(lease.key, str):
            raise ValueError("expected a submission check job")
        if lease.completedStage == "submission_parsed" and lease.stageData is not None:
            version = lease.stageData.get("parserVersion", 1)
            if version == SUBMISSION_PARSE_VERSION:
                return solutionStepsAdapter.validate_python(lease.stageData["steps"])
            if version != 1:
                raise ValueError("unsupported submission parser checkpoint version")
        elif lease.completedStage is not None:
            raise ValueError("unexpected checkpoint for submission parsing")
        submitted = AttemptSubmittedInput.model_validate(lease.payload)
        with Session(self.engine) as session:
            assignment = session.get(AssignmentJob, (submitted.assignmentId, submitted.assignmentRev))
            if assignment is None or assignment.stageData is None or "tasks" not in assignment.stageData:
                raise ValueError("parsed tasks are unavailable for the submitted assignment revision")
            tasks = parsedTasksAdapter.validate_python(assignment.stageData["tasks"])
        steps = readSubmission(submitted, loadDocument(submitted.docRef), tasks)
        data = {"parserVersion": SUBMISSION_PARSE_VERSION, "steps": solutionStepsAdapter.dump_python(steps, mode="json")}
        if not self.jobs.checkpoint(lease, "submission_parsed", data):
            raise RuntimeError("check lease expired before the submission parse could be saved")
        return steps
