"""Cabinet tables and the shared ownership check for class operations."""

from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Select, String, func, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.database import Base
from shared.httperrors import ApiProblem


class ClassRecord(Base):
    __tablename__ = "classes"

    classId: Mapped[str] = mapped_column("class_id", String(36), primary_key=True)
    teacherId: Mapped[str] = mapped_column(
        "teacher_id", ForeignKey("cabinet.teachers.teacher_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column("name", String(120))
    deletedAt: Mapped[datetime | None] = mapped_column("deleted_at", DateTime(timezone=True))


class StudentRecord(Base):
    __tablename__ = "students"

    studentId: Mapped[str] = mapped_column("student_id", String(36), primary_key=True)
    classId: Mapped[str] = mapped_column(
        "class_id", ForeignKey("cabinet.classes.class_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column("name", String(200))
    deletedAt: Mapped[datetime | None] = mapped_column("deleted_at", DateTime(timezone=True))


def activeStudents(classId: str) -> Select[StudentRecord]:
    return select(StudentRecord).where(StudentRecord.classId == classId, StudentRecord.deletedAt.is_(None))


def lockStudentCreation(db: Session, classId: str) -> None:
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"cabinet.students/{classId}", 0))))


def requireOwnedClass(db: Session, classId: str, teacherId: str, *, lockForWrite: bool = False) -> None:
    if len(classId) == 36 and "\x00" not in classId:
        query = select(ClassRecord.classId).where(
            ClassRecord.classId == classId, ClassRecord.teacherId == teacherId, ClassRecord.deletedAt.is_(None),
        )
        owned = db.scalar(query.with_for_update(read=True) if lockForWrite else query)
        if owned is not None:
            return
    raise ApiProblem("not_found", "Class not found")


def deleteClass(db: Session, classId: str, teacherId: str, deletedAt: datetime) -> None:
    if len(classId) == 36 and "\x00" not in classId:
        removed = db.scalar(
            update(ClassRecord).where(
                ClassRecord.classId == classId, ClassRecord.teacherId == teacherId, ClassRecord.deletedAt.is_(None),
            ).values(deletedAt=deletedAt).returning(ClassRecord.classId)
        )
        if removed is not None:
            return
    raise ApiProblem("not_found", "Class not found")


def deleteStudent(db: Session, classId: str, studentId: str, teacherId: str, deletedAt: datetime) -> None:
    requireOwnedClass(db, classId, teacherId, lockForWrite=True)
    if len(studentId) == 36 and "\x00" not in studentId:
        removed = db.scalar(
            update(StudentRecord).where(
                StudentRecord.classId == classId, StudentRecord.studentId == studentId, StudentRecord.deletedAt.is_(None),
            ).values(deletedAt=deletedAt).returning(StudentRecord.studentId)
        )
        if removed is not None:
            return
    raise ApiProblem("not_found", "Student not found in the class")
