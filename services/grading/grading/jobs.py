"""Claim durable work and commit progress only while its lease is current."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, cast

from sqlalchemy import DateTime, Engine, and_, func, or_, select, update
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from grading.tables import AssignmentJob, CheckJob

JobKey = str | tuple[str, int]
JobModel = type[CheckJob] | type[AssignmentJob]
FinalState = Literal["ready", "needs_review", "failed"]
RetryState = Literal["queued", "waiting_reference"]
LEASE_DURATION = timedelta(seconds=60)


@dataclass(frozen=True)
class JobLease:
    key: JobKey
    version: int
    expiresAt: datetime
    traceId: str
    payload: dict[str, object]
    completedStage: str | None
    stageData: dict[str, object] | None


class JobQueue:
    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None) -> None:
        self.engine = engine
        self.clock = clock

    def claimCheck(self, *, leaseFor: timedelta = LEASE_DURATION) -> JobLease | None:
        return self._claim(CheckJob, leaseFor)

    def claimAssignment(self, *, leaseFor: timedelta = LEASE_DURATION) -> JobLease | None:
        return self._claim(AssignmentJob, leaseFor)

    def _claim(self, model: JobModel, leaseFor: timedelta) -> JobLease | None:
        if leaseFor <= timedelta(0):
            raise ValueError("lease duration must be positive")
        with Session(self.engine) as session, session.begin():
            now = self.clock() if self.clock is not None else session.execute(
                select(func.clock_timestamp(type_=DateTime(timezone=True)))
            ).scalar_one()
            due = or_(
                and_(
                    model.state.in_(("queued", "waiting_reference")),
                    or_(model.nextAttemptAt.is_(None), model.nextAttemptAt <= now),
                ),
                and_(model.state == "running", model.leaseUntil <= now),
            )
            query = (
                select(model).where(due)
                .order_by(func.coalesce(model.nextAttemptAt, model.leaseUntil).nulls_last(), *model.__mapper__.primary_key)
                .limit(1).with_for_update(skip_locked=True)
            )
            job = cast(CheckJob | AssignmentJob | None, session.scalars(query).one_or_none())
            if job is None:
                return None
            key = job.checkId if isinstance(job, CheckJob) else (job.assignmentId, job.assignmentRev)
            expiresAt = now + leaseFor
            job.state = "running"
            job.leaseVersion += 1
            job.leaseUntil = expiresAt
            job.nextAttemptAt = None
            return JobLease(key, job.leaseVersion, expiresAt, job.traceId, job.payload, job.completedStage, job.stageData)

    def checkpoint(self, lease: JobLease, stage: str, data: dict[str, object] | None = None) -> bool:
        return self._write(lease, {"completedStage": stage, "stageData": data})

    def finish(self, lease: JobLease, state: FinalState, stage: str, data: dict[str, object] | None = None) -> bool:
        return self._write(lease, {"state": state, "completedStage": stage, "stageData": data, "leaseUntil": None})

    def reschedule(self, lease: JobLease, state: RetryState, nextAttemptAt: datetime) -> bool:
        return self._write(lease, {"state": state, "nextAttemptAt": nextAttemptAt, "leaseUntil": None})

    def waitForReference(self, lease: JobLease, delay: timedelta) -> bool:
        now = self.clock() if self.clock is not None else func.clock_timestamp()
        return self._write(lease, {"state": "waiting_reference", "nextAttemptAt": now + delay, "leaseUntil": None})

    def _write(self, lease: JobLease, values: dict[str, object]) -> bool:
        if isinstance(lease.key, str):
            model: JobModel = CheckJob
            matchesKey: ColumnElement[bool] = CheckJob.checkId == lease.key
        else:
            model = AssignmentJob
            assignmentId, assignmentRev = lease.key
            matchesKey = and_(AssignmentJob.assignmentId == assignmentId, AssignmentJob.assignmentRev == assignmentRev)
        now = self.clock() if self.clock is not None else func.clock_timestamp()
        statement = (
            update(model)
            .where(matchesKey, model.state == "running", model.leaseVersion == lease.version, model.leaseUntil > now)
            .values(**values)
            .returning(model.leaseVersion)
        )
        with Session(self.engine) as session, session.begin():
            return session.execute(statement).scalar_one_or_none() is not None
