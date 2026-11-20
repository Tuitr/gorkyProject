"""Solver builds and published solver versions in the PostgreSQL schema `refs` (developer 3 only).

refs.solver_builds: one row per task key (statement and constraints). The builder takes a queued row with a lease,
asks the author for independent solvers, and closes the row as published or failed. A failed row is final for
its task key, so the paid model is not asked again for the same statement.

refs.solver_versions: one row per published version. The value is what the admitted solver computed in the
sandbox for this task; the code and the cross-check behind it are in S3 (contracts SolverArtifact).
A withdrawn version is never used again, but old registry records keep its solverVersion.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, Index, Integer, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from references.database import Base


class SolverBuildRecord(Base):
    __tablename__ = "solver_builds"
    __table_args__ = (
        CheckConstraint("state IN ('queued', 'running', 'published', 'failed')", name="build_state_known"),
        CheckConstraint("state <> 'published' OR solver_version IS NOT NULL", name="published_has_version"),
        CheckConstraint("state <> 'failed' OR reason_code IS NOT NULL", name="failed_has_reason"),
        CheckConstraint("state = 'running' OR lease_token IS NULL", name="only_running_has_lease"),
        CheckConstraint("model_calls >= 0", name="model_calls_not_negative"),
        Index("solver_builds_open", "not_before", postgresql_where=text("state IN ('queued', 'running')")),
    )

    buildId: Mapped[str] = mapped_column("build_id", String(64), primary_key=True)
    taskKey: Mapped[str] = mapped_column("task_key", String(80), unique=True)
    statement: Mapped[str] = mapped_column("statement", Text)
    constraints: Mapped[dict[str, Any]] = mapped_column("constraints", JSONB)
    state: Mapped[str] = mapped_column("state", String(16))
    reasonCode: Mapped[str | None] = mapped_column("reason_code", String(64))
    modelCalls: Mapped[int] = mapped_column("model_calls", Integer, default=0)
    leaseToken: Mapped[str | None] = mapped_column("lease_token", String(64))
    leaseUntil: Mapped[datetime | None] = mapped_column("lease_until", DateTime(timezone=True))
    notBefore: Mapped[datetime | None] = mapped_column("not_before", DateTime(timezone=True))
    """A queued build is not taken before this time (a provider pause)."""
    deadline: Mapped[datetime] = mapped_column("deadline", DateTime(timezone=True))
    solverVersion: Mapped[str | None] = mapped_column("solver_version", String(128))
    log: Mapped[list[dict[str, Any]]] = mapped_column("log", JSONB, default=list)
    """Hashes, codes and model ids only: never the statement, the code or the model output."""
    createdAt: Mapped[datetime] = mapped_column("created_at", DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SolverVersionRecord(Base):
    __tablename__ = "solver_versions"
    __table_args__ = (
        UniqueConstraint("family_id", "version_no", name="solver_versions_family_no"),
        CheckConstraint("version_no >= 1", name="version_no_positive"),
        CheckConstraint("source IN ('generated', 'manual')", name="version_source_known"),
    )

    solverVersion: Mapped[str] = mapped_column("solver_version", String(128), primary_key=True)
    familyId: Mapped[str] = mapped_column("family_id", String(64))
    versionNo: Mapped[int] = mapped_column("version_no", Integer)
    taskKey: Mapped[str] = mapped_column("task_key", String(80), index=True)
    source: Mapped[str] = mapped_column("source", String(16))
    schemaId: Mapped[str] = mapped_column("schema_id", String(64))
    value: Mapped[dict[str, Any]] = mapped_column("value", JSONB)
    codeSha256: Mapped[str] = mapped_column("code_sha256", String(64))
    testsSha256: Mapped[str] = mapped_column("tests_sha256", String(64))
    metadataKey: Mapped[str] = mapped_column("metadata_key", String(512))
    withdrawn: Mapped[bool] = mapped_column("withdrawn", Boolean, default=False, server_default="false")
    publishedAt: Mapped[datetime] = mapped_column("published_at", DateTime(timezone=True))
