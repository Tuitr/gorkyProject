"""Reading and writing registry records, and turning them into contract results."""

from collections.abc import Callable

from sqlalchemy import inspect, select
from sqlalchemy.dialects.postgresql import insert
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

    def addOrGet(self, record: ReferenceRecord) -> ReferenceRecord:
        """Store the record unless its lookup key is taken, then return the row that is stored.

        INSERT ... ON CONFLICT DO NOTHING waits while another transaction inserts the same key,
        so parallel requests from any process end with one row and one referenceId.
        A SELECT before the INSERT could not promise that.
        """
        state = inspect(record)
        values = {
            attribute.key: state.dict[attribute.key]
            for attribute in state.mapper.column_attrs
            if attribute.key in state.dict
        }
        statement = insert(ReferenceRecord).values(values).on_conflict_do_nothing(index_elements=["lookup_key"])
        with self.sessionFactory() as session, session.begin():
            session.execute(statement)
            stored = session.scalars(select(ReferenceRecord).where(ReferenceRecord.lookupKey == record.lookupKey)).one()
            session.expunge(stored)
        return stored

    def get(self, referenceId: str) -> ReferenceRecord | None:
        with self.sessionFactory() as session:
            record = session.get(ReferenceRecord, referenceId)
            if record is not None:
                session.expunge(record)
            return record
