"""Keep a durable draft until its exact Markdown object can be published."""

from collections.abc import Callable
from datetime import datetime
from typing import Literal

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from contracts.v1.grading.checkresult import CheckOutcome, CheckStatus, MarkdownRef, PublishedCheck
from contracts.v1.grading.taskresult import TaskResult
from grading.intake import AttemptSubmittedInput
from grading.jobs import JobLease
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
            return CheckOutcome.model_validate(row.outcome) if row is not None else None

    def saveDraft(
        self, checkId: str, calculationId: str, taskResults: tuple[TaskResult, ...], overallReview: str, *,
        lease: JobLease | None = None,
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
            if existing is not None:
                if outcome != CheckOutcome.model_validate(existing.outcome):
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
            if resultRev is None:
                row = latestRevision(session, checkId)
            else:
                row = session.get(CheckResultRevision, (checkId, resultRev))
                if row is None:
                    return None
            if row is None:
                return pendingStatus(job)
            if row.markdownRef is None:
                return CheckStatus(checkId=checkId, state="running", reason="awaiting_artifact")
            ref = MarkdownRef.model_validate(row.markdownRef)
            result = publishedResult(row.outcome, ref)
        requireArtifact(ref)
        return result


def publishedResult(outcome: dict[str, object], ref: MarkdownRef) -> PublishedCheck:
    check = CheckOutcome.model_validate(outcome)
    state: Literal["ready", "needs_review"] = "needs_review" if any(task.needsReview for task in check.taskResults) else "ready"
    return PublishedCheck(
        checkId=check.checkId, attemptId=check.attemptId, resultRev=check.resultRev, taskResults=check.taskResults,
        overallReview=check.overallReview, state=state, markdownRef=ref,
    )


def pendingStatus(job: CheckJob) -> CheckStatus:
    match job.state:
        case "queued":
            return CheckStatus(checkId=job.checkId, state="queued", reason="queued_for_grading")
        case "waiting_reference":
            return CheckStatus(checkId=job.checkId, state="waiting_reference", reason="awaiting_reference")
        case "needs_review":
            return CheckStatus(checkId=job.checkId, state="needs_review", reason=job.completedStage or "manual_review")
        case "failed":
            return CheckStatus(checkId=job.checkId, state="failed", reason="grading_failed")
        case _:
            return CheckStatus(checkId=job.checkId, state="running", reason="grading_in_progress")
