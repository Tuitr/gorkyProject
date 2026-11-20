"""Cabinet tables and the shared ownership check for class operations."""

from sqlalchemy import ForeignKey, String, select
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


class StudentRecord(Base):
    __tablename__ = "students"

    studentId: Mapped[str] = mapped_column("student_id", String(36), primary_key=True)
    classId: Mapped[str] = mapped_column(
        "class_id", ForeignKey("cabinet.classes.class_id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column("name", String(200))


def requireOwnedClass(db: Session, classId: str, teacherId: str) -> None:
    if len(classId) == 36 and "\x00" not in classId:
        owned = db.scalar(
            select(ClassRecord.classId).where(ClassRecord.classId == classId, ClassRecord.teacherId == teacherId)
        )
        if owned is not None:
            return
    raise ApiProblem("not_found", "Class not found")
