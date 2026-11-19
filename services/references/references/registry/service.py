"""Registry logic behind POST /internal/v1/references."""

import uuid
from collections.abc import Callable

from sqlalchemy import select

from contracts.v1.references.reference import ReferenceRequest, ReferenceResult
from references.providers.fixture import FixtureProvider, ReferenceFixture
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
            return recordToResult(existing)
        record = self.buildRecord(request, self.provider.find(request))
        self.store.add(record)
        return recordToResult(record)

    def findSame(self, request: ReferenceRequest) -> ReferenceRecord | None:
        """Same assignment revision, task, statement and constraints reuse one referenceId."""
        with self.store.sessionFactory() as session:
            query = select(ReferenceRecord).where(
                ReferenceRecord.assignmentId == request.assignmentId,
                ReferenceRecord.assignmentRev == request.assignmentRev,
                ReferenceRecord.taskId == request.taskId,
                ReferenceRecord.statement == request.statement,
                ReferenceRecord.constraints == request.constraints,
            )
            record = session.scalars(query).first()
            if record is not None:
                session.expunge(record)
            return record

    def buildRecord(self, request: ReferenceRequest, fixture: ReferenceFixture | None) -> ReferenceRecord:
        record = ReferenceRecord(
            referenceId=self.idFactory(),
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
        applyOutcome(record, fixture)
        return record


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
