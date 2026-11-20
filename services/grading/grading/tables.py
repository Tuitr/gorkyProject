"""Durable inbox and queued work in the grading schema."""

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKeyConstraint, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from grading.database import Base
from grading.settings import DB_SCHEMA


class JobProgress:
    completedStage: Mapped[str | None] = mapped_column("completed_stage", Text)
    stageData: Mapped[dict[str, object] | None] = mapped_column("stage_data", JSONB(none_as_null=True))
    leaseUntil: Mapped[datetime | None] = mapped_column("lease_until", DateTime(timezone=True))
    leaseVersion: Mapped[int] = mapped_column("lease_version", BigInteger, server_default="0")
    nextAttemptAt: Mapped[datetime | None] = mapped_column("next_attempt_at", DateTime(timezone=True))
    state: Mapped[str] = mapped_column(Text, server_default="queued")


class AssignmentJob(JobProgress, Base):
    __tablename__ = "assignment_jobs"
    __table_args__ = (
        CheckConstraint("assignment_rev >= 1", name="assignment_jobs_positive_rev"),
        CheckConstraint(
            "state IN ('queued', 'waiting_reference', 'running', 'ready', 'needs_review', 'failed')",
            name="assignment_jobs_known_state",
        ),
    )

    assignmentId: Mapped[str] = mapped_column("assignment_id", Text, primary_key=True)
    assignmentRev: Mapped[int] = mapped_column("assignment_rev", BigInteger, primary_key=True)
    traceId: Mapped[str] = mapped_column("trace_id", Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class CheckJob(JobProgress, Base):
    __tablename__ = "check_jobs"
    __table_args__ = (
        CheckConstraint("assignment_rev >= 1", name="check_jobs_positive_rev"),
        CheckConstraint(
            "state IN ('queued', 'waiting_reference', 'running', 'ready', 'needs_review', 'failed')",
            name="check_jobs_known_state",
        ),
    )

    checkId: Mapped[str] = mapped_column("check_id", Text, primary_key=True)
    assignmentId: Mapped[str] = mapped_column("assignment_id", Text)
    assignmentRev: Mapped[int] = mapped_column("assignment_rev", BigInteger)
    traceId: Mapped[str] = mapped_column("trace_id", Text)
    payload: Mapped[dict[str, object]] = mapped_column(JSONB)


class CheckResultRevision(Base):
    __tablename__ = "check_result_revisions"
    __table_args__ = (
        CheckConstraint("result_rev >= 1", name="check_result_revisions_positive_rev"),
        UniqueConstraint("check_id", "calculation_id", name="check_result_revisions_calculation"),
        ForeignKeyConstraint(["check_id"], [f"{DB_SCHEMA}.check_jobs.check_id"]),
    )

    checkId: Mapped[str] = mapped_column("check_id", Text, primary_key=True)
    resultRev: Mapped[int] = mapped_column("result_rev", BigInteger, primary_key=True)
    calculationId: Mapped[str] = mapped_column("calculation_id", Text)
    outcome: Mapped[dict[str, object]] = mapped_column(JSONB)
    markdownRef: Mapped[dict[str, object] | None] = mapped_column("markdown_ref", JSONB(none_as_null=True))


class InboxEntry(Base):
    __tablename__ = "inbox"
    # An event claims its ID before creating its job. The links are checked at commit.
    __table_args__ = (
        ForeignKeyConstraint(
            ["check_id"], [f"{DB_SCHEMA}.check_jobs.check_id"],
            name="inbox_check_job", deferrable=True, initially="DEFERRED",
        ),
        ForeignKeyConstraint(
            ["assignment_id", "assignment_rev"],
            [f"{DB_SCHEMA}.assignment_jobs.assignment_id", f"{DB_SCHEMA}.assignment_jobs.assignment_rev"],
            name="inbox_assignment_job", deferrable=True, initially="DEFERRED",
        ),
        CheckConstraint(
            "(event_type = 'attempt.submitted' AND check_id IS NOT NULL"
            " AND assignment_id IS NULL AND assignment_rev IS NULL) OR"
            " (event_type = 'assignment.published' AND check_id IS NULL"
            " AND assignment_id IS NOT NULL AND assignment_rev IS NOT NULL)",
            name="inbox_one_job",
        ),
    )

    eventId: Mapped[str] = mapped_column("event_id", Text, primary_key=True)
    eventType: Mapped[str] = mapped_column("event_type", Text)
    traceId: Mapped[str] = mapped_column("trace_id", Text)
    occurredAt: Mapped[datetime] = mapped_column("occurred_at", DateTime(timezone=True))
    checkId: Mapped[str | None] = mapped_column("check_id", Text)
    assignmentId: Mapped[str | None] = mapped_column("assignment_id", Text)
    assignmentRev: Mapped[int | None] = mapped_column("assignment_rev", BigInteger)
