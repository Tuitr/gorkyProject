"""Registry logic behind POST /internal/v1/references."""

import math
import uuid
from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import select

from contracts.v1.references.reference import ReferenceRequest, ReferenceResult
from references.providers.base import PendingOutcome, ProviderOutcome, ProviderTask, ReadyOutcome, ReferenceProvider
from references.registry.keys import buildLookupKey
from references.registry.store import ReferenceStore, recordToResult
from references.registry.tables import ReferenceRecord
from shared.clock import Clock, SystemClock


def newReferenceId() -> str:
    return f"ref_{uuid.uuid4().hex}"


class ReferenceService:
    def __init__(
        self,
        store: ReferenceStore,
        provider: ReferenceProvider,
        clock: Clock | None = None,
        idFactory: Callable[[], str] = newReferenceId,
    ) -> None:
        self.store = store
        self.provider = provider
        self.clock = clock if clock is not None else SystemClock()
        self.idFactory = idFactory

    def request(self, request: ReferenceRequest) -> ReferenceResult:
        """Parallel calls with one lookup key ask the provider once and share one referenceId."""
        record = self.findSame(request)
        if record is None:
            record = self.store.addOnce(
                buildLookupKey(request),
                lambda: self.buildRecord(request, self.provider.start(ProviderTask.fromRequest(request))),
            )
        return self.refresh(record)

    def get(self, referenceId: str) -> ReferenceResult | None:
        """Read the stored state; GET never creates a record or a new referenceId."""
        record = self.store.get(referenceId)
        return None if record is None else self.refresh(record)

    def refresh(self, record: ReferenceRecord) -> ReferenceResult:
        """Once the promised wait of a pending record has passed, ask the provider again.

        check() runs while the row is locked, so parallel GETs and POSTs of one record ask the provider once;
        the others wait for the lock and read the new state.
        """
        if not self.isDue(record):
            return self.toResult(record)
        with self.store.sessionFactory() as session, session.begin():
            current = session.get(ReferenceRecord, record.referenceId, with_for_update=True)
            assert current is not None
            if self.isDue(current):
                self.applyOutcome(current, self.provider.check(taskFromRecord(current)))
            session.flush()
            session.expunge(current)
        return self.toResult(current)

    def isDue(self, record: ReferenceRecord) -> bool:
        return record.state == "pending" and record.readyAt is not None and self.clock.now() >= record.readyAt

    def toResult(self, record: ReferenceRecord) -> ReferenceResult:
        retryAfterS = 0
        if record.state == "pending" and record.readyAt is not None:
            remaining = (record.readyAt - self.clock.now()).total_seconds()
            retryAfterS = max(1, math.ceil(remaining))
        return recordToResult(record, retryAfterS)

    def findSame(self, request: ReferenceRequest) -> ReferenceRecord | None:
        """Fast path for a repeated request, without a lock. Uniqueness itself comes from ReferenceStore.addOnce."""
        with self.store.sessionFactory() as session:
            query = select(ReferenceRecord).where(ReferenceRecord.lookupKey == buildLookupKey(request))
            record = session.scalars(query).first()
            if record is not None:
                session.expunge(record)
            return record

    def buildRecord(self, request: ReferenceRequest, outcome: ProviderOutcome) -> ReferenceRecord:
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
        self.applyOutcome(record, outcome)
        return record

    def applyOutcome(self, record: ReferenceRecord, outcome: ProviderOutcome) -> None:
        record.state = outcome.state
        if isinstance(outcome, PendingOutcome):
            record.readyAt = self.clock.now() + timedelta(seconds=outcome.retryAfterS)
            return
        record.readyAt = None
        if isinstance(outcome, ReadyOutcome):
            record.source = outcome.source
            record.solverVersion = outcome.solverVersion
            record.schemaId = outcome.schemaId
            record.value = dict(outcome.value)
        else:
            record.reasonCode = outcome.reasonCode


def taskFromRecord(record: ReferenceRecord) -> ProviderTask:
    return ProviderTask(
        assignmentRev=record.assignmentRev,
        statement=record.statement,
        constraints=record.constraints,
        familyHint=record.familyHint,
    )
