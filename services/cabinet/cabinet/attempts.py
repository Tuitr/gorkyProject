"""Keep the original submission separate from reviews of assignment revisions."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.database import Base
from contracts.v1.events.checks import DocRef


class AttemptRecord(Base):
    __tablename__ = "attempts"
    __table_args__ = (UniqueConstraint("assignment_id", "student_id", "sha256", name="uq_attempts_submission"),)

    attemptId: Mapped[str] = mapped_column("attempt_id", String(36), primary_key=True)
    assignmentId: Mapped[str] = mapped_column("assignment_id", String(36))
    studentId: Mapped[str] = mapped_column("student_id", ForeignKey("cabinet.students.student_id"))
    sha256: Mapped[str] = mapped_column(String(64))
    bucket: Mapped[str] = mapped_column(String(63))
    objectKey: Mapped[str] = mapped_column("object_key", String(255))
    submittedAt: Mapped[datetime] = mapped_column("submitted_at", DateTime(timezone=True))


class CheckRecord(Base):
    __tablename__ = "attempt_checks"
    __table_args__ = (
        UniqueConstraint("attempt_id", "assignment_rev", name="uq_attempt_checks_revision"),
        ForeignKeyConstraint(
            ["assignment_id", "assignment_rev"],
            ["cabinet.assignments.assignment_id", "cabinet.assignments.assignment_rev"],
        ),
    )

    checkId: Mapped[str] = mapped_column("check_id", String(36), primary_key=True)
    attemptId: Mapped[str] = mapped_column("attempt_id", ForeignKey("cabinet.attempts.attempt_id"))
    assignmentId: Mapped[str] = mapped_column("assignment_id", String(36))
    assignmentRev: Mapped[int] = mapped_column("assignment_rev")


def ensureAttempt(
    db: Session, assignmentId: str, studentId: str, ref: DocRef, submittedAt: datetime,
) -> str:
    attemptId = db.scalar(
        insert(AttemptRecord)
        .values(
            attemptId=str(uuid.uuid4()), assignmentId=assignmentId, studentId=studentId,
            sha256=ref.sha256, bucket=ref.bucket, objectKey=ref.key, submittedAt=submittedAt,
        )
        .on_conflict_do_nothing(constraint="uq_attempts_submission")
        .returning(AttemptRecord.attemptId)
    )
    if attemptId is None:
        # A concurrent insert may have won. Read the committed attempt in a new statement.
        attemptId = db.scalar(
            select(AttemptRecord.attemptId).where(
                AttemptRecord.assignmentId == assignmentId,
                AttemptRecord.studentId == studentId,
                AttemptRecord.sha256 == ref.sha256,
            )
        )
        assert attemptId is not None
    return attemptId


def ensureCheck(db: Session, attemptId: str, assignmentId: str, assignmentRev: int) -> str:
    checkId = db.scalar(
        insert(CheckRecord)
        .values(checkId=str(uuid.uuid4()), attemptId=attemptId, assignmentId=assignmentId, assignmentRev=assignmentRev)
        .on_conflict_do_nothing(constraint="uq_attempt_checks_revision")
        .returning(CheckRecord.checkId)
    )
    if checkId is None:
        checkId = db.scalar(
            select(CheckRecord.checkId).where(
                CheckRecord.attemptId == attemptId, CheckRecord.assignmentRev == assignmentRev,
            )
        )
        assert checkId is not None
    return checkId
