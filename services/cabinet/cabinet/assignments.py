"""Immutable assignment revisions and their source documents."""

import uuid
from contextlib import closing
from datetime import date

from sqlalchemy import Date, ForeignKey, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.classes import ClassRecord
from cabinet.database import Base
from cabinet.documents import buildS3Client, storeDocx
from cabinet.settings import Settings
from contracts.v1.cabinet.assignments import AssignmentVersion
from contracts.v1.events.checks import TeacherRequirement
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
    bucket: Mapped[str] = mapped_column(String(63))
    objectKey: Mapped[str] = mapped_column("object_key", String(255))
    sha256: Mapped[str] = mapped_column(String(64))


def storeAssignment(
    db: Session,
    settings: Settings,
    classId: str,
    assignmentId: str | None,
    assignmentDate: date,
    requirements: list[TeacherRequirement],
    original: bytes,
) -> AssignmentVersion:
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

    key = f"assignments/{assignmentId}/{revision}/{uuid.uuid4().hex}.docx"
    with closing(buildS3Client(settings)) as client:
        docRef = storeDocx(client, key, original)
    db.add(AssignmentRecord(
        assignmentId=assignmentId,
        assignmentRev=revision,
        classId=classId,
        assignmentDate=assignmentDate,
        requirements=[{"text": item.text, "taskNumber": item.taskNumber} for item in requirements],
        bucket=docRef.bucket,
        objectKey=docRef.key,
        sha256=docRef.sha256,
    ))
    return AssignmentVersion(assignmentId=assignmentId, assignmentRev=revision)


def requireOwnedAssignment(db: Session, assignmentId: str, assignmentRev: int, teacherId: str) -> str:
    if len(assignmentId) == 36 and "\x00" not in assignmentId:
        classId = db.scalar(
            select(AssignmentRecord.classId)
            .join(ClassRecord, ClassRecord.classId == AssignmentRecord.classId)
            .where(
                AssignmentRecord.assignmentId == assignmentId,
                AssignmentRecord.assignmentRev == assignmentRev,
                ClassRecord.teacherId == teacherId,
            )
        )
        if classId is not None:
            return classId
    raise ApiProblem("not_found", "Assignment revision not found")
