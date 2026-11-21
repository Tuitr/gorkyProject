"""Keep the original submission separate from reviews of assignment revisions."""

import hashlib
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, ForeignKeyConstraint, Index, SmallInteger, String, UniqueConstraint, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.assignments import AssignmentRecord
from cabinet.classes import ClassRecord
from cabinet.database import Base
from cabinet.outbox import EventContext, enqueueEvent
from contracts.v1.events.checks import AttemptSubmittedEvent, AttemptSubmittedPayload, DocRef
from shared.httperrors import ApiProblem


class AttemptRecord(Base):
    __tablename__ = "attempts"
    __table_args__ = (
        Index("uq_attempts_submission", "assignment_id", "student_id", "submission_hash", unique=True,
              postgresql_where=text("deleted_at IS NULL")),
    )

    attemptId: Mapped[str] = mapped_column("attempt_id", String(36), primary_key=True)
    assignmentId: Mapped[str] = mapped_column("assignment_id", String(36))
    studentId: Mapped[str] = mapped_column("student_id", ForeignKey("cabinet.students.student_id"))
    sha256: Mapped[str] = mapped_column(String(64))
    submissionHash: Mapped[str] = mapped_column("submission_hash", String(64))
    bucket: Mapped[str] = mapped_column(String(63))
    objectKey: Mapped[str] = mapped_column("object_key", String(255))
    submittedAt: Mapped[datetime] = mapped_column("submitted_at", DateTime(timezone=True))
    deletedAt: Mapped[datetime | None] = mapped_column("deleted_at", DateTime(timezone=True))


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
    grade: Mapped[int | None] = mapped_column(SmallInteger)
    """The teacher's grade from 2 to 5. Older rows may contain a 1."""


def ensureAttempt(
    db: Session, assignmentId: str, studentId: str, ref: DocRef, submittedAt: datetime,
    *, pageRefs: list[DocRef] | None = None,
) -> str:
    submissionHash = ref.sha256
    if pageRefs is not None:
        hashes = b"".join(bytes.fromhex(page.sha256) for page in pageRefs)
        submissionHash = hashlib.sha256(b"cabinet.photo-pages.v1\0" + hashes).hexdigest()
    # Lock the existing active attempt on a duplicate so concurrent deletion cannot archive it mid-binding.
    return db.execute(
        insert(AttemptRecord)
        .values(
            attemptId=str(uuid.uuid4()), assignmentId=assignmentId, studentId=studentId,
            sha256=ref.sha256, submissionHash=submissionHash, bucket=ref.bucket, objectKey=ref.key, submittedAt=submittedAt,
        )
        .on_conflict_do_update(
            index_elements=[AttemptRecord.assignmentId, AttemptRecord.studentId, AttemptRecord.submissionHash],
            index_where=AttemptRecord.deletedAt.is_(None),
            set_={"submission_hash": AttemptRecord.submissionHash},
        )
        .returning(AttemptRecord.attemptId)
    ).scalar_one()


def deleteAttempt(db: Session, attemptId: str, teacherId: str, deletedAt: datetime, confirm: bool) -> None:
    if len(attemptId) != 36 or "\x00" in attemptId:
        raise ApiProblem("not_found", "Submission not found")
    attempt = db.scalar(
        select(AttemptRecord)
        .join(AssignmentRecord, (AssignmentRecord.assignmentId == AttemptRecord.assignmentId) & (AssignmentRecord.assignmentRev == 1))
        .join(ClassRecord, ClassRecord.classId == AssignmentRecord.classId)
        .where(
            AttemptRecord.attemptId == attemptId, AttemptRecord.deletedAt.is_(None),
            AssignmentRecord.deletedAt.is_(None), ClassRecord.teacherId == teacherId, ClassRecord.deletedAt.is_(None),
        )
        .with_for_update(of=AttemptRecord)
    )
    if attempt is None:
        raise ApiProblem("not_found", "Submission not found")
    if not confirm:
        raise ApiProblem("confirmation_required", "Confirm deleting the submission", status=409)
    attempt.deletedAt = deletedAt


def ensureCheck(
    db: Session, attemptId: str, assignmentId: str, assignmentRev: int,
    studentId: str, ref: DocRef, eventContext: EventContext,
    *, pageRefs: list[DocRef] | None = None,
) -> str:
    checkId = db.scalar(
        insert(CheckRecord)
        .values(checkId=str(uuid.uuid4()), attemptId=attemptId, assignmentId=assignmentId, assignmentRev=assignmentRev)
        .on_conflict_do_nothing(constraint="uq_attempt_checks_revision")
        .returning(CheckRecord.checkId)
    )
    if checkId is not None:
        enqueueEvent(db, AttemptSubmittedEvent(
            v=1, type="attempt.submitted", eventId=str(uuid.uuid4()),
            traceId=eventContext.traceId, occurredAt=eventContext.occurredAt,
            payload=AttemptSubmittedPayload(
                assignmentId=assignmentId, assignmentRev=assignmentRev, docRef=ref,
                studentId=studentId, attemptId=attemptId, checkId=checkId, pageRefs=pageRefs,
            ),
        ))
    else:
        checkId = db.scalar(
            select(CheckRecord.checkId).where(
                CheckRecord.attemptId == attemptId, CheckRecord.assignmentRev == assignmentRev,
            )
        )
        assert checkId is not None
    return checkId
