"""Keep a durable draft until its exact Markdown object can be published."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from pydantic import TypeAdapter
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from contracts.v1.grading.checkresult import CheckOutcome, CheckStatus, MarkdownRef, PhotoMark, PhotoPage, PublishedCheck
from contracts.v1.grading.taskresult import TaskResult
from grading.intake import AttemptSubmittedInput
from grading.jobs import JobLease
from grading.photoOcr import TranscribedLine, renderTranscript
from grading.submission import SubmissionReading
from grading.scoring import storedOutcome, withScores
from grading.tables import CheckJob, CheckResultRevision


class ResultConflict(ValueError):
    """The calculation or publication would change an existing revision."""


class ArtifactUnavailable(RuntimeError):
    """The result object is missing, changed or temporarily inaccessible."""


def latestRevision(session: Session, checkId: str) -> CheckResultRevision | None:
    return session.scalar(
        select(CheckResultRevision).filter_by(checkId=checkId).order_by(CheckResultRevision.resultRev.desc()).limit(1)
    )


class ResultStore:
    def __init__(self, engine: Engine, clock: Callable[[], datetime] | None = None) -> None:
        self.engine = engine
        self.clock = clock

    def findDraft(self, checkId: str, calculationId: str) -> CheckOutcome | None:
        with Session(self.engine) as session:
            row = session.scalar(select(CheckResultRevision).filter_by(checkId=checkId, calculationId=calculationId))
            return storedOutcome(row.outcome) if row is not None else None

    def saveDraft(
        self, checkId: str, calculationId: str, taskResults: tuple[TaskResult, ...], overallReview: str, *,
        lease: JobLease | None = None, maxima: dict[str, float] | None = None,
        omittedTasks: frozenset[str] = frozenset(),
        answerScoredTasks: frozenset[str] = frozenset(),
    ) -> CheckOutcome:
        if not calculationId or not taskResults or len({task.taskId for task in taskResults}) != len(taskResults):
            raise ValueError("expected an identified calculation with distinct task results")
        with Session(self.engine) as session, session.begin():
            job = session.get_one(CheckJob, checkId, with_for_update=True)
            if lease is not None:
                now = self.clock() if self.clock is not None else session.scalar(select(func.clock_timestamp()))
                if (lease.key != checkId or lease.version != job.leaseVersion or job.state != "running"
                        or job.leaseUntil is None or now is None or job.leaseUntil <= now):
                    raise ResultConflict("check lease expired before saving its result")
            existing = session.scalar(select(CheckResultRevision).filter_by(checkId=checkId, calculationId=calculationId))
            latest = latestRevision(session, checkId)
            submitted = AttemptSubmittedInput.model_validate(job.payload)
            outcome = CheckOutcome(
                checkId=checkId, attemptId=submitted.attemptId, taskResults=list(taskResults), overallReview=overallReview,
                resultRev=existing.resultRev if existing is not None else (latest.resultRev + 1 if latest is not None else 1),
            )
            outcome = withScores(outcome, maxima or {}, {}, omittedTasks=omittedTasks, answerScoredTasks=answerScoredTasks)
            if existing is not None:
                if outcome != storedOutcome(existing.outcome):
                    raise ResultConflict("the calculation already has a different result")
                return outcome
            if latest is not None and latest.markdownRef is None:
                raise ResultConflict("publish the previous revision before saving another calculation")
            session.add(CheckResultRevision(
                checkId=checkId, resultRev=outcome.resultRev, calculationId=calculationId,
                outcome=outcome.model_dump(mode="json"),
            ))
            return outcome

    def publish(self, checkId: str, resultRev: int, ref: MarkdownRef,
                requireArtifact: Callable[[MarkdownRef], None]) -> PublishedCheck:
        requireArtifact(ref)
        with Session(self.engine) as session, session.begin():
            job = session.get_one(CheckJob, checkId, with_for_update=True)
            row = session.get_one(CheckResultRevision, (checkId, resultRev))
            result = publishedResult(row.outcome, ref)
            if row.markdownRef is not None:
                if MarkdownRef.model_validate(row.markdownRef) != ref:
                    raise ResultConflict("the published revision already has a different Markdown object")
                return result
            row.markdownRef = ref.model_dump(mode="json")
            job.state = result.state
            job.completedStage = "result_published"
            job.leaseUntil = None
            job.nextAttemptAt = None
            return result

    def recordAcceptance(self, checkId: str, resultRev: int, *, actorId: str | None = None) -> None:
        """Record a teacher's choice once. Automatic Markdown publication does not accept an attempt."""
        with Session(self.engine) as session, session.begin():
            row = session.get_one(CheckResultRevision, (checkId, resultRev), with_for_update=True)
            if row.markdownRef is None:
                raise ResultConflict("the result must be published before it can be accepted")
            if row.acceptedAt is None:
                row.acceptedAt = self.clock() if self.clock is not None else session.execute(
                    select(func.clock_timestamp())
                ).scalar_one()
                row.acceptedBy = actorId

    def acceptedReference(self, checkId: str, resultRev: int) -> MarkdownRef | None:
        with Session(self.engine) as session:
            row = session.get(CheckResultRevision, (checkId, resultRev))
            if row is None or row.acceptedAt is None or row.markdownRef is None:
                return None
            return MarkdownRef.model_validate(row.markdownRef)

    def read(self, checkId: str, resultRev: int | None,
             requireArtifact: Callable[[MarkdownRef], None]) -> CheckStatus | PublishedCheck | None:
        with Session(self.engine) as session:
            job = session.get(CheckJob, checkId)
            if job is None:
                return None
            checkpoint = job.stageData or {}
            if resultRev is None:
                row = latestRevision(session, checkId)
            else:
                row = session.get(CheckResultRevision, (checkId, resultRev))
                if row is None:
                    return None
            ocr = photoDetails(checkpoint, CheckOutcome.model_validate(row.outcome) if row is not None else None)
            if row is None:
                return pendingStatus(job).model_copy(update=ocr)
            if row.markdownRef is None:
                return CheckStatus(checkId=checkId, attemptId=CheckOutcome.model_validate(row.outcome).attemptId,
                                   state="running", reason="awaiting_artifact").model_copy(update=ocr)
            ref = MarkdownRef.model_validate(row.markdownRef)
            accepted = session.scalar(
                select(CheckResultRevision.resultRev)
                .where(CheckResultRevision.checkId == checkId, CheckResultRevision.acceptedAt.is_not(None))
                .order_by(CheckResultRevision.acceptedAt.desc(), CheckResultRevision.resultRev.desc()).limit(1)
            )
            result = publishedResult(row.outcome, ref).model_copy(update={"publishedResultRev": accepted, **ocr})
        requireArtifact(ref)
        return result


readingAdapter = TypeAdapter(SubmissionReading)
linesAdapter = TypeAdapter(list[tuple[TranscribedLine, ...]])


def photoDetails(checkpoint: dict[str, object], outcome: CheckOutcome | None) -> dict[str, object]:
    if "ocrLines" in checkpoint:
        pages = [PhotoPage(pageIndex=i, transcriptMarkdown=renderTranscript(lines))
                 for i, lines in enumerate(linesAdapter.validate_python(checkpoint["ocrLines"]))]
        return {"photoPages": pages, "transcriptMarkdown": "\n".join(p.transcriptMarkdown for p in pages)}
    transcript = checkpoint.get("transcriptMarkdown")
    if not isinstance(transcript, str):
        return {}
    if not checkpoint.get("photoPages"):
        return {"transcriptMarkdown": transcript}
    reading = readingAdapter.validate_python(checkpoint)
    anchors = {(step.taskId, step.stepId): step.sourceAnchor for step in reading.steps}
    marks = [
        PhotoMark(taskId=result.taskId, issueId=issue.issueId, pageIndex=anchor.pageIndex,
                  lineIndex=anchor.paragraphIndex, box=anchor.box)
        for result in (outcome.taskResults if outcome else []) for issue in result.issues
        if (anchor := anchors.get((result.taskId, issue.stepId))) is not None
    ]
    details: dict[str, object] = {"transcriptMarkdown": transcript, "photoPages": list(reading.photoPages),
                                  "ocrStatus": reading.ocrStatus, "photoMarks": marks}
    detail = reading.ocrReason
    if outcome is not None and reading.exactAnswerTaskIds and not reading.unassignedFragments:
        unresolved = {task.taskId for task in outcome.taskResults if task.needsReview}
        remaining = next((task for task in reading.taskReadings if task.taskId in unresolved and task.reviewReason), None)
        if not unresolved and outcome.total is not None and outcome.total.pendingCount == 0:
            details["ocrStatus"] = "readable"
            detail = None
        elif remaining is not None:
            detail = f"Задание № {remaining.taskId.removeprefix('t')}. {remaining.reviewReason}"
    if detail:
        details["ocrReason"] = detail
    return details


def publishedResult(outcome: dict[str, object], ref: MarkdownRef) -> PublishedCheck:
    check = storedOutcome(outcome)
    state: Literal["ready", "needs_review"] = "needs_review" if any(task.needsReview for task in check.taskResults) else "ready"
    return PublishedCheck(
        **check.model_dump(), state=state, markdownRef=ref,
    )


def pendingStatus(job: CheckJob) -> CheckStatus:
    attemptId = AttemptSubmittedInput.model_validate(job.payload).attemptId
    match job.state:
        case "queued":
            return CheckStatus(checkId=job.checkId, attemptId=attemptId, state="queued", reason="queued_for_grading")
        case "waiting_reference":
            return CheckStatus(checkId=job.checkId, attemptId=attemptId, state="waiting_reference",
                               reason="awaiting_reference")
        case "needs_review":
            return CheckStatus(checkId=job.checkId, attemptId=attemptId, state="needs_review",
                               reason=job.completedStage or "manual_review")
        case "failed":
            reason = job.completedStage if job.completedStage and job.completedStage.startswith("ocr_") else "grading_failed"
            return CheckStatus(checkId=job.checkId, attemptId=attemptId, state="failed", reason=reason)
        case _:
            return CheckStatus(checkId=job.checkId, attemptId=attemptId, state="running", reason="grading_in_progress")
