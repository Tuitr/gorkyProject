"""Reading and writing registry records, and turning them into contract results."""

from collections.abc import Callable

from sqlalchemy import inspect, select, text
from sqlalchemy.dialects.postgresql import Insert, insert
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


LOCK_KEY = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")
"""Held until the transaction ends. Two keys with the same hash only wait for each other, which is harmless."""


def insertStatement(record: ReferenceRecord) -> Insert:
    state = inspect(record)
    values = {
        attribute.key: state.dict[attribute.key] for attribute in state.mapper.column_attrs if attribute.key in state.dict
    }
    return insert(ReferenceRecord).values(values).on_conflict_do_nothing(index_elements=["lookup_key"])


class ReferenceStore:
    def __init__(self, sessionFactory: SessionFactory) -> None:
        self.sessionFactory = sessionFactory

    def add(self, record: ReferenceRecord) -> None:
        with self.sessionFactory() as session, session.begin():
            session.add(record)

    def addOnce(self, lookupKey: str, build: Callable[[], ReferenceRecord]) -> ReferenceRecord:
        """Return the row stored under lookupKey; build and store it first if there is none.

        A transaction-scoped advisory lock on the key makes parallel requests from any process wait for
        each other, so build() (the provider call) runs once per key. The INSERT keeps ON CONFLICT DO NOTHING:
        a writer that does not take the lock still ends with one row and one referenceId.
        """
        with self.sessionFactory() as session, session.begin():
            session.execute(LOCK_KEY, {"key": lookupKey})
            stored = self.findByKey(session, lookupKey)
            if stored is None:
                record = build()
                if record.lookupKey != lookupKey:
                    raise ValueError("build() returned a record for another lookup key")
                session.execute(insertStatement(record))
                stored = self.findByKey(session, lookupKey)
                assert stored is not None
            session.expunge(stored)
        return stored

    def findByKey(self, session: Session, lookupKey: str) -> ReferenceRecord | None:
        return session.scalars(select(ReferenceRecord).where(ReferenceRecord.lookupKey == lookupKey)).first()

    def get(self, referenceId: str) -> ReferenceRecord | None:
        with self.sessionFactory() as session:
            record = session.get(ReferenceRecord, referenceId)
            if record is not None:
                session.expunge(record)
            return record
