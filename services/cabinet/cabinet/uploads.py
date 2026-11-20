"""Uploads and the original student documents they contain."""

import uuid

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.database import Base
from cabinet.documents import storeDocx
from cabinet.settings import Settings
from contracts.v1.cabinet.uploads import UploadAccepted
from shared.httperrors import ApiProblem


class UploadRecord(Base):
    __tablename__ = "uploads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["assignment_id", "assignment_rev"],
            ["cabinet.assignments.assignment_id", "cabinet.assignments.assignment_rev"],
        ),
    )

    uploadId: Mapped[str] = mapped_column("upload_id", String(36), primary_key=True)
    assignmentId: Mapped[str] = mapped_column("assignment_id", String(36))
    assignmentRev: Mapped[int] = mapped_column("assignment_rev")


class UploadFileRecord(Base):
    __tablename__ = "upload_files"
    __table_args__ = (UniqueConstraint("object_key"),)

    fileId: Mapped[str] = mapped_column("file_id", String(36), primary_key=True)
    uploadId: Mapped[str] = mapped_column(
        "upload_id", ForeignKey("cabinet.uploads.upload_id", ondelete="CASCADE"), index=True
    )
    originalName: Mapped[str] = mapped_column("original_name", String(255))
    bucket: Mapped[str] = mapped_column(String(63))
    objectKey: Mapped[str] = mapped_column("object_key", String(255))
    sha256: Mapped[str] = mapped_column(String(64))


def storeUpload(
    db: Session,
    settings: Settings,
    assignmentId: str,
    assignmentRev: int,
    filename: str | None,
    original: bytes,
) -> UploadAccepted:
    originalName = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if not originalName.strip() or len(originalName) > 255 or "\x00" in originalName:
        raise ApiProblem("invalid_input", "Expected a document filename of at most 255 characters")

    uploadId = str(uuid.uuid4())
    fileId = str(uuid.uuid4())
    docRef = storeDocx(settings, f"uploads/{uploadId}/{fileId}.docx", original)
    db.add(UploadRecord(uploadId=uploadId, assignmentId=assignmentId, assignmentRev=assignmentRev))
    db.flush()
    db.add(UploadFileRecord(
        fileId=fileId, uploadId=uploadId, originalName=originalName,
        bucket=docRef.bucket, objectKey=docRef.key, sha256=docRef.sha256,
    ))
    return UploadAccepted(uploadId=uploadId)
