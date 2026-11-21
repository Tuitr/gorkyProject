"""Immutable assignment revisions and their source documents."""

import uuid
from contextlib import closing
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, String, UniqueConstraint, func, select, update
from sqlalchemy.dialects.postgresql import JSONB, distinct_on
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.classes import ClassRecord
from cabinet.database import Base
from cabinet.documents import buildS3Client, storeDocx
from cabinet.outbox import EventContext, enqueueEvent
from cabinet.settings import Settings
from contracts.v1.cabinet.assignments import AssignmentSummary, AssignmentVersion
from contracts.v1.events.checks import AssignmentPublishedEvent, AssignmentPublishedPayload, Subject, TeacherRequirement
from shared.httperrors import ApiProblem


class AssignmentRecord(Base):
    __tablename__ = "assignments"
    __table_args__ = (UniqueConstraint("object_key"),)

    assignmentId: Mapped[str] = mapped_column("assignment_id", String(36), primary_key=True)
    assignmentRev: Mapped[int] = mapped_column("assignment_rev", primary_key=True)
    classId: Mapped[str] = mapped_column(
        "class_id", ForeignKey("cabinet.classes.class_id", ondelete="CASCADE"), index=True
    )
    assignmentDate: Mapped[date] = mapped_column("assignment_date", Date)
    requirements: Mapped[list[dict[str, str | None]]] = mapped_column(JSONB)
    subject: Mapped[str] = mapped_column(String(16), server_default="math")
    title: Mapped[str | None] = mapped_column(String(120))
    bucket: Mapped[str] = mapped_column(String(63))
    objectKey: Mapped[str] = mapped_column("object_key", String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    deletedAt: Mapped[datetime | None] = mapped_column("deleted_at", DateTime(timezone=True))


def listActiveAssignments(db: Session, classId: str) -> list[AssignmentSummary]:
    latest = (
        select(
            AssignmentRecord.assignmentId, AssignmentRecord.assignmentRev, AssignmentRecord.assignmentDate,
            AssignmentRecord.subject, AssignmentRecord.title,
        )
        .where(AssignmentRecord.classId == classId, AssignmentRecord.deletedAt.is_(None))
        .ext(distinct_on(AssignmentRecord.assignmentId))
        .order_by(AssignmentRecord.assignmentId, AssignmentRecord.assignmentRev.desc())
        .subquery()
    )
    rows = db.execute(
        select(latest.c.assignmentId, latest.c.assignmentRev, latest.c.assignmentDate, latest.c.subject, latest.c.title)
        .order_by(latest.c.assignmentDate.desc(), latest.c.assignmentId)
    )
    return [
        AssignmentSummary(
            assignmentId=assignmentId, assignmentRev=revision, assignmentDate=assignmentDate,
            subject="physics" if subject == "physics" else "math", title=title,
        )
        for assignmentId, revision, assignmentDate, subject, title in rows
    ]


def storeAssignment(
    db: Session,
    settings: Settings,
    classId: str,
    assignmentId: str | None,
    assignmentDate: date,
    requirements: list[TeacherRequirement],
    original: bytes,
    eventContext: EventContext,
    subject: Subject | None = None,
    title: str | None = None,
) -> AssignmentVersion:
    """subject None keeps the subject of the previous revision; a new assignment without one is math.
    title None keeps the title of the previous revision; a new assignment without one has no title."""
    if any("\x00" in item.text or "\x00" in (item.taskNumber or "") for item in requirements):
        raise ApiProblem("invalid_input", "Requirements must not contain null characters")

    revision = 1
    if assignmentId is None:
        assignmentId = str(uuid.uuid4())
    else:
        # Every update of an assignment takes the same lock before selecting the next revision.
        root = db.scalar(
            select(AssignmentRecord.assignmentId)
            .where(
                AssignmentRecord.assignmentId == assignmentId,
                AssignmentRecord.assignmentRev == 1,
                AssignmentRecord.classId == classId,
                AssignmentRecord.deletedAt.is_(None),
            )
            .with_for_update()
        )
        if root is None:
            raise ApiProblem("not_found", "Assignment not found")
        latest = db.scalar(
            select(func.max(AssignmentRecord.assignmentRev)).where(AssignmentRecord.assignmentId == assignmentId)
        )
        assert latest is not None
        revision = latest + 1
        previous = db.execute(
            select(AssignmentRecord.subject, AssignmentRecord.title).where(
                AssignmentRecord.assignmentId == assignmentId, AssignmentRecord.assignmentRev == latest,
            )
        ).one()
        if subject is None:
            subject = "physics" if previous.subject == "physics" else "math"
        if title is None:
            title = previous.title
    if subject is None:
        subject = "math"

    key = f"assignments/{assignmentId}/{revision}/{uuid.uuid4().hex}.docx"
    with closing(buildS3Client(settings)) as client:
        docRef = storeDocx(client, key, original)
    db.add(AssignmentRecord(
        assignmentId=assignmentId,
        assignmentRev=revision,
        classId=classId,
        assignmentDate=assignmentDate,
        requirements=[{"text": item.text, "taskNumber": item.taskNumber} for item in requirements],
        subject=subject,
        title=title,
        bucket=docRef.bucket,
        objectKey=docRef.key,
        sha256=docRef.sha256,
    ))
    enqueueEvent(db, AssignmentPublishedEvent(
        v=1, type="assignment.published", eventId=str(uuid.uuid4()),
        traceId=eventContext.traceId, occurredAt=eventContext.occurredAt,
        payload=AssignmentPublishedPayload(
            assignmentId=assignmentId, assignmentRev=revision, docRef=docRef, requirements=requirements,
            subject=subject,
        ),
    ))
    return AssignmentVersion(assignmentId=assignmentId, assignmentRev=revision)


def requireOwnedAssignment(
    db: Session, assignmentId: str, assignmentRev: int, teacherId: str, *, lockForWrite: bool = False,
) -> str:
    if len(assignmentId) == 36 and "\x00" not in assignmentId:
        query = (
            select(AssignmentRecord.classId)
            .join(ClassRecord, ClassRecord.classId == AssignmentRecord.classId)
            .where(
                AssignmentRecord.assignmentId == assignmentId,
                AssignmentRecord.assignmentRev == assignmentRev,
                ClassRecord.teacherId == teacherId,
                ClassRecord.deletedAt.is_(None),
                AssignmentRecord.deletedAt.is_(None),
            )
        )
        classId = db.scalar(query.with_for_update(read=True, of=(ClassRecord, AssignmentRecord)) if lockForWrite else query)
        if classId is not None:
            return classId
    raise ApiProblem("not_found", "Assignment revision not found")


def deleteAssignment(db: Session, assignmentId: str, teacherId: str, deletedAt: datetime, confirm: bool) -> None:
    if len(assignmentId) != 36 or "\x00" in assignmentId:
        raise ApiProblem("not_found", "Assignment not found")
    # Share the revision-one lock used by storeAssignment before updating every revision.
    root = db.scalar(
        select(AssignmentRecord.assignmentId)
        .join(ClassRecord, ClassRecord.classId == AssignmentRecord.classId)
        .where(
            AssignmentRecord.assignmentId == assignmentId, AssignmentRecord.assignmentRev == 1,
            AssignmentRecord.deletedAt.is_(None), ClassRecord.teacherId == teacherId, ClassRecord.deletedAt.is_(None),
        )
        .with_for_update(of=AssignmentRecord)
    )
    if root is None:
        raise ApiProblem("not_found", "Assignment not found")
    if not confirm:
        raise ApiProblem("confirmation_required", "Confirm deleting the assignment and all its submissions", status=409)
    db.execute(update(AssignmentRecord).where(AssignmentRecord.assignmentId == root).values(deletedAt=deletedAt))
