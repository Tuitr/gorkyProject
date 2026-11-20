"""refs.solver_builds and refs.solver_versions: the provider opens builds and reads versions, the builder runs them.

A build is leased like a review call (C3.3): lease_token and lease_until say who works on it and until when.
A builder that crashed frees the build when the lease runs out; a write with an old token changes nothing.
"""

import hashlib
import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from pydantic import JsonValue
from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from references.registry.keys import canonicalJson
from references.registry.store import LOCK_KEY, SessionFactory
from references.solvers.tables import SolverBuildRecord, SolverVersionRecord

TASK_KEY_VERSION = "t1"
OPEN_STATES = ("queued", "running")


def taskKeyOf(statement: str, constraints: dict[str, JsonValue]) -> str:
    """One solver per exact task: the same statement and constraints in any assignment share it."""
    payload = canonicalJson({"statement": statement, "constraints": constraints})
    return f"{TASK_KEY_VERSION}:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def familyIdOf(taskKey: str) -> str:
    """The SolverArtifact familyId of a per-task solver: task<16 hex>, a valid FamilyId."""
    return "task" + taskKey.split(":", 1)[1][:16]


@dataclass(frozen=True)
class BuildView:
    buildId: str
    taskKey: str
    statement: str
    constraints: dict[str, JsonValue]
    state: str
    reasonCode: str | None
    deadline: datetime
    leaseUntil: datetime | None
    solverVersion: str | None
    log: list[dict[str, JsonValue]]

    @classmethod
    def of(cls, record: SolverBuildRecord) -> "BuildView":
        return cls(
            buildId=record.buildId,
            taskKey=record.taskKey,
            statement=record.statement,
            constraints=dict(record.constraints),
            state=record.state,
            reasonCode=record.reasonCode,
            deadline=record.deadline,
            leaseUntil=record.leaseUntil,
            solverVersion=record.solverVersion,
            log=list(record.log),
        )


@dataclass(frozen=True)
class Lease:
    buildId: str
    token: str


@dataclass(frozen=True)
class VersionView:
    solverVersion: str
    schemaId: str
    value: dict[str, JsonValue]
    source: str


@dataclass(frozen=True)
class NewVersion:
    """What the builder publishes; the version number is chosen by publish()."""

    taskKey: str
    source: str
    schemaId: str
    value: dict[str, JsonValue]
    codeSha256: str
    testsSha256: str


class SolverStore:
    def __init__(self, sessionFactory: SessionFactory) -> None:
        self.sessionFactory = sessionFactory

    # Provider side

    def activeVersion(self, taskKey: str) -> VersionView | None:
        query = (
            select(SolverVersionRecord)
            .where(SolverVersionRecord.taskKey == taskKey, SolverVersionRecord.withdrawn.is_(False))
            .order_by(SolverVersionRecord.versionNo.desc())
            .limit(1)
        )
        with self.sessionFactory() as session:
            record = session.scalars(query).first()
            if record is None:
                return None
            return VersionView(record.solverVersion, record.schemaId, dict(record.value), record.source)

    def ensureBuild(
        self, statement: str, constraints: dict[str, JsonValue], now: datetime, deadlineS: float
    ) -> BuildView:
        """The build of this task, opened now if there is none. Parallel calls open one build."""
        taskKey = taskKeyOf(statement, constraints)
        row: dict[str, Any] = {
            "buildId": f"bld_{uuid.uuid4().hex}",
            "taskKey": taskKey,
            "statement": statement,
            "constraints": constraints,
            "state": "queued",
            "modelCalls": 0,
            "deadline": now + timedelta(seconds=deadlineS),
            "log": [],
        }
        with self.sessionFactory() as session, session.begin():
            session.execute(insert(SolverBuildRecord).values(row).on_conflict_do_nothing(index_elements=["task_key"]))
            record = session.scalars(select(SolverBuildRecord).where(SolverBuildRecord.taskKey == taskKey)).one()
            return BuildView.of(record)

    def expireIfAbandoned(self, buildId: str, now: datetime) -> BuildView:
        """Past its deadline with nobody holding a live lease, an open build fails as generation_timeout."""
        with self.sessionFactory() as session, session.begin():
            session.execute(
                update(SolverBuildRecord)
                .where(
                    SolverBuildRecord.buildId == buildId,
                    SolverBuildRecord.state.in_(OPEN_STATES),
                    SolverBuildRecord.deadline <= now,
                    or_(SolverBuildRecord.leaseUntil.is_(None), SolverBuildRecord.leaseUntil <= now),
                )
                .values(state="failed", reasonCode="generation_timeout", leaseToken=None, leaseUntil=None)
            )
            record = session.get(SolverBuildRecord, buildId)
            assert record is not None
            return BuildView.of(record)

    # Builder side

    def claim(self, now: datetime, leaseS: float) -> tuple[Lease, BuildView] | None:
        """Take the oldest queued build that may run now, or a running one whose lease ran out."""
        query = (
            select(SolverBuildRecord)
            .where(
                or_(
                    (SolverBuildRecord.state == "queued")
                    & or_(SolverBuildRecord.notBefore.is_(None), SolverBuildRecord.notBefore <= now),
                    (SolverBuildRecord.state == "running") & (SolverBuildRecord.leaseUntil <= now),
                )
            )
            .order_by(SolverBuildRecord.createdAt)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        with self.sessionFactory() as session, session.begin():
            record = session.scalars(query).first()
            if record is None:
                return None
            record.state = "running"
            record.leaseToken = uuid.uuid4().hex
            record.leaseUntil = now + timedelta(seconds=leaseS)
            record.notBefore = None
            return Lease(record.buildId, record.leaseToken), BuildView.of(record)

    def extend(self, lease: Lease, now: datetime, leaseS: float) -> bool:
        with self.sessionFactory() as session, session.begin():
            result = session.execute(
                update(SolverBuildRecord)
                .where(*self.owned(lease))
                .values(leaseUntil=now + timedelta(seconds=leaseS))
            )
            return bool(result.rowcount)  # type: ignore[attr-defined]

    def pause(self, lease: Lease, notBefore: datetime, log: list[dict[str, JsonValue]], modelCalls: int) -> bool:
        """Back to the queue until notBefore (the provider asked to wait)."""
        return self.close(lease, log, modelCalls, state="queued", notBefore=notBefore)

    def fail(self, lease: Lease, reasonCode: str, log: list[dict[str, JsonValue]], modelCalls: int) -> bool:
        return self.close(lease, log, modelCalls, state="failed", reasonCode=reasonCode)

    def publish(
        self, lease: Lease, version: NewVersion, solverVersion: str, versionNo: int, metadataKey: str,
        publishedAt: datetime, log: list[dict[str, JsonValue]], modelCalls: int,
    ) -> bool:
        """The version row and the published build in one transaction, only while the lease is still ours."""
        with self.sessionFactory() as session, session.begin():
            build = session.scalars(select(SolverBuildRecord).where(*self.owned(lease)).with_for_update()).first()
            if build is None:
                return False
            session.add(
                SolverVersionRecord(
                    solverVersion=solverVersion,
                    familyId=familyIdOf(version.taskKey),
                    versionNo=versionNo,
                    taskKey=version.taskKey,
                    source=version.source,
                    schemaId=version.schemaId,
                    value=version.value,
                    codeSha256=version.codeSha256,
                    testsSha256=version.testsSha256,
                    metadataKey=metadataKey,
                    withdrawn=False,
                    publishedAt=publishedAt,
                )
            )
            build.state = "published"
            build.solverVersion = solverVersion
            build.leaseToken = None
            build.leaseUntil = None
            build.log = log
            build.modelCalls += modelCalls
            return True

    def nextVersionNo(self, familyId: str) -> int:
        with self.sessionFactory() as session, session.begin():
            session.execute(LOCK_KEY, {"key": f"solver-version:{familyId}"})
            numbers = session.scalars(
                select(SolverVersionRecord.versionNo).where(SolverVersionRecord.familyId == familyId)
            ).all()
            return max(numbers, default=0) + 1

    def close(
        self, lease: Lease, log: list[dict[str, JsonValue]], modelCalls: int, *, state: str,
        reasonCode: str | None = None, notBefore: datetime | None = None,
    ) -> bool:
        with self.sessionFactory() as session, session.begin():
            build = session.scalars(select(SolverBuildRecord).where(*self.owned(lease)).with_for_update()).first()
            if build is None:
                return False
            build.state = state
            build.reasonCode = reasonCode
            build.notBefore = notBefore
            build.leaseToken = None
            build.leaseUntil = None
            build.log = log
            build.modelCalls += modelCalls
            return True

    @staticmethod
    def owned(lease: Lease) -> tuple[Any, ...]:
        return (
            SolverBuildRecord.buildId == lease.buildId,
            SolverBuildRecord.state == "running",
            SolverBuildRecord.leaseToken == lease.token,
        )


def retryAfterOf(build: BuildView, now: datetime, pollS: int) -> int:
    """retryAfterS for a reference waiting on this build: the poll interval, never past the deadline by much."""
    leftS = (build.deadline - now).total_seconds()
    return max(1, min(pollS, math.ceil(leftS) if leftS > 0 else pollS))
