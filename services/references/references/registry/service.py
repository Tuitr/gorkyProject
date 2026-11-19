"""Registry logic behind POST /internal/v1/references."""

import math
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select

from contracts.v1.references.reference import ReferenceRequest, ReferenceResult
from references.providers.fixture import FixtureProvider, ReferenceFixture
from references.registry.keys import buildLookupKey
from references.registry.store import ReferenceStore, recordToResult
from references.registry.tables import ReferenceRecord
from shared.clock import Clock, SystemClock

UNKNOWN_TASK = "unknown_task"


def newReferenceId() -> str:
    return f"ref_{uuid.uuid4().hex}"


class ReferenceService:
    def __init__(
        self,
        store: ReferenceStore,
        provider: FixtureProvider,
        clock: Clock | None = None,
        idFactory: Callable[[], str] = newReferenceId,
    ) -> None:
        self.store = store
        self.provider = provider
        self.clock = clock if clock is not None else SystemClock()
        self.idFactory = idFactory

    def request(self, request: ReferenceRequest) -> ReferenceResult:
        existing = self.findSame(request)
        if existing is not None:
            return self.refresh(existing)
        record = self.buildRecord(request, self.provider.find(request))
        self.store.add(record)
        return self.toResult(record)

    def get(self, referenceId: str) -> ReferenceResult | None:
        """Read the stored state; GET never creates a record or a new referenceId."""
        record = self.store.get(referenceId)
        return None if record is None else self.refresh(record)

    def refresh(self, record: ReferenceRecord) -> ReferenceResult:
        """A pending fixture becomes final once its controlled delay has passed."""
        if record.state != "pending" or record.readyAt is None or self.clock.now() < record.readyAt:
            return self.toResult(record)
        fixture = self.provider.find(requestFromRecord(record))
        with self.store.sessionFactory() as session, session.begin():
            current = session.get(ReferenceRecord, record.referenceId, with_for_update=True)
            assert current is not None
            if current.state == "pending":
                if fixture is None:
                    current.state = "unsupported"
                    current.reasonCode = UNKNOWN_TASK
                else:
                    applyOutcome(current, fixture)
                current.readyAt = None
            session.flush()
            session.expunge(current)
        return self.toResult(current)

    def toResult(self, record: ReferenceRecord) -> ReferenceResult:
        retryAfterS = 0
        if record.state == "pending" and record.readyAt is not None:
            remaining = (record.readyAt - self.clock.now()).total_seconds()
            retryAfterS = max(1, math.ceil(remaining))
        return recordToResult(record, retryAfterS)

    def findSame(self, request: ReferenceRequest) -> ReferenceRecord | None:
        """Requests with the same lookup key reuse one referenceId."""
        with self.store.sessionFactory() as session:
            query = select(ReferenceRecord).where(ReferenceRecord.lookupKey == buildLookupKey(request))
            record = session.scalars(query).first()
            if record is not None:
                session.expunge(record)
            return record

    def buildRecord(self, request: ReferenceRequest, fixture: ReferenceFixture | None) -> ReferenceRecord:
        record = ReferenceRecord(
            referenceId=self.idFactory(),
            lookupKey=buildLookupKey(request),
            assignmentId=request.assignmentId,
            assignmentRev=request.assignmentRev,
            taskId=request.taskId,
            statement=request.statement,
            constraints=dict(request.constraints),
            familyHint=request.familyHint,
        )
        if fixture is None:
            record.state = "unsupported"
            record.reasonCode = UNKNOWN_TASK
            return record
        if fixture.pendingForS > 0:
            record.state = "pending"
            record.readyAt = self.clock.now() + timedelta(seconds=fixture.pendingForS)
            return record
        applyOutcome(record, fixture)
        return record


def requestFromRecord(record: ReferenceRecord) -> ReferenceRequest:
    return ReferenceRequest(
        assignmentId=record.assignmentId,
        assignmentRev=record.assignmentRev,
        taskId=record.taskId,
        statement=record.statement,
        constraints=record.constraints,
        familyHint=record.familyHint,
    )


def applyOutcome(record: ReferenceRecord, fixture: ReferenceFixture) -> None:
    outcome = fixture.outcome
    record.state = outcome.state
    if outcome.state == "ready":
        record.source = "fixture"
        record.solverVersion = None
        record.schemaId = outcome.schemaId
        record.value = dict(outcome.value or {})
    else:
        record.reasonCode = outcome.reasonCode
