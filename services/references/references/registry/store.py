"""Reading and writing registry records, and turning them into contract results."""

from collections.abc import Callable

from sqlalchemy.orm import Session

from contracts.v1.references.reference import (
    ReferencePending,
    ReferenceReady,
    ReferenceResult,
    ReferenceUnsupported,
    ReferenceValidationFailed,
)
from references.registry.tables import ReferenceRecord

SessionFactory = Callable[[], Session]


def recordToResult(record: ReferenceRecord, retryAfterS: int = 0) -> ReferenceResult:
    """Validate the stored row against contracts v1. A malformed row raises instead of leaking out."""
    if record.state == "ready":
        return ReferenceReady.model_validate(
            {
                "referenceId": record.referenceId,
                "state": "ready",
                "source": record.source,
                "schemaId": record.schemaId,
                "value": record.value,
                "solverVersion": record.solverVersion,
            }
        )
    if record.state == "pending":
        return ReferencePending(referenceId=record.referenceId, state="pending", retryAfterS=retryAfterS)
    if record.state == "unsupported":
        return ReferenceUnsupported.model_validate(
            {"referenceId": record.referenceId, "state": "unsupported", "reasonCode": record.reasonCode}
        )
    if record.state == "validation_failed":
        return ReferenceValidationFailed.model_validate(
            {"referenceId": record.referenceId, "state": "validation_failed", "reasonCode": record.reasonCode}
        )
    raise ValueError(f"unknown stored state {record.state!r}")


class ReferenceStore:
    def __init__(self, sessionFactory: SessionFactory) -> None:
        self.sessionFactory = sessionFactory

    def add(self, record: ReferenceRecord) -> None:
        with self.sessionFactory() as session, session.begin():
            session.add(record)

    def get(self, referenceId: str) -> ReferenceRecord | None:
        with self.sessionFactory() as session:
            record = session.get(ReferenceRecord, referenceId)
            if record is not None:
                session.expunge(record)
            return record
