"""Reference registry table in the PostgreSQL schema `refs` (owned by developer 3 only).

Python attributes are camelCase, SQL columns keep snake_case.
A ready record always has schema_id, source and value; a refused one has reason_code and no value,
so a refusal can never be read as a mathematical zero.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, MetaData, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from references.settings import DB_SCHEMA


class Base(DeclarativeBase):
    metadata = MetaData(schema=DB_SCHEMA)


class ReferenceRecord(Base):
    __tablename__ = "reference_records"
    __table_args__ = (
        CheckConstraint(
            "state IN ('ready', 'pending', 'unsupported', 'validation_failed')", name="state_known"
        ),
        CheckConstraint(
            "state <> 'ready' OR (schema_id IS NOT NULL AND source IS NOT NULL AND value IS NOT NULL"
            " AND reason_code IS NULL)",
            name="ready_has_value",
        ),
        CheckConstraint(
            "state NOT IN ('unsupported', 'validation_failed') OR (reason_code IS NOT NULL AND value IS NULL)",
            name="refusal_has_reason",
        ),
        CheckConstraint(
            "source IS NULL OR source = 'fixture' OR solver_version IS NOT NULL",
            name="non_fixture_has_solver_version",
        ),
        CheckConstraint("assignment_rev >= 1", name="assignment_rev_positive"),
    )

    referenceId: Mapped[str] = mapped_column("reference_id", String(64), primary_key=True)
    lookupKey: Mapped[str] = mapped_column("lookup_key", String(80), index=True, unique=True)
    assignmentId: Mapped[str] = mapped_column("assignment_id", String(128))
    assignmentRev: Mapped[int] = mapped_column("assignment_rev", Integer)
    taskId: Mapped[str] = mapped_column("task_id", String(128))
    statement: Mapped[str] = mapped_column("statement", Text)
    constraints: Mapped[dict[str, Any]] = mapped_column("constraints", JSONB)
    familyHint: Mapped[str | None] = mapped_column("family_hint", String(128))
    state: Mapped[str] = mapped_column("state", String(32))
    source: Mapped[str | None] = mapped_column("source", String(32))
    solverVersion: Mapped[str | None] = mapped_column("solver_version", String(128))
    schemaId: Mapped[str | None] = mapped_column("schema_id", String(64))
    value: Mapped[dict[str, Any] | None] = mapped_column("value", JSONB(none_as_null=True))
    reasonCode: Mapped[str | None] = mapped_column("reason_code", String(64))
    readyAt: Mapped[datetime | None] = mapped_column("ready_at", DateTime(timezone=True))
    createdAt: Mapped[datetime] = mapped_column("created_at", DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
