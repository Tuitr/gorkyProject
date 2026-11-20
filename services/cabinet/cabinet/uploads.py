"""Uploads and the original student documents they contain."""

import stat
import uuid
from contextlib import closing
from dataclasses import dataclass
from os import SEEK_END
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo
from zlib import error as ZlibError

from fastapi import UploadFile

from sqlalchemy import ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.database import Base
from cabinet.documents import MAX_DOCX_BYTES, buildS3Client, readDocx, storeDocx, validateDocx
from cabinet.settings import Settings
from contracts.v1.cabinet.uploads import UploadAccepted
from shared.httperrors import ApiProblem


MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_DOCX_FILES = 100
MAX_ZIP_ENTRIES = 200


@dataclass(frozen=True)
class SubmissionDocument:
    originalName: str
    content: bytes


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
    status: Mapped[str] = mapped_column(String(32), server_default="pending_binding")


def submissionName(filename: str) -> str:
    if not filename.strip() or len(filename) > 255 or "\x00" in filename:
        raise ApiProblem("invalid_input", "Expected a document filename of at most 255 characters")
    return filename


def readSubmission(upload: UploadFile) -> list[SubmissionDocument]:
    filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if filename.lower().endswith(".zip"):
        return readSubmissionZip(upload)
    return [SubmissionDocument(submissionName(filename), readDocx(upload))]


def zipMemberName(member: ZipInfo) -> str:
    path = member.orig_filename
    components = path.removesuffix("/").split("/")
    if "\x00" in path or "\\" in path or ":" in path or any(part in {"", ".", ".."} for part in components):
        raise ApiProblem("invalid_input", "ZIP contains an invalid path")

    expectedType = stat.S_IFDIR if member.is_dir() else stat.S_IFREG
    if (
        stat.S_IFMT(member.external_attr >> 16) not in {0, expectedType}
        or member.compress_type not in {ZIP_STORED, ZIP_DEFLATED}
        or member.flag_bits & 1
    ):
        raise ApiProblem("invalid_input", "ZIP contains an unsupported entry")
    return components[-1]


def readSubmissionZip(upload: UploadFile) -> list[SubmissionDocument]:
    if upload.file.seek(0, SEEK_END) > MAX_ZIP_BYTES:
        raise ApiProblem("file_too_large", "ZIP exceeds the upload size limit", status=413)

    documents: list[SubmissionDocument] = []
    remaining = MAX_UNPACKED_BYTES
    try:
        with ZipFile(upload.file) as archive:
            members = archive.infolist()
            if len(members) > MAX_ZIP_ENTRIES:
                raise ApiProblem("file_too_large", "ZIP contains too many entries", status=413)
            for member in members:
                name = zipMemberName(member)
                if member.is_dir():
                    if member.file_size:
                        raise ApiProblem("invalid_input", "ZIP contains an invalid directory")
                    continue
                if not name.lower().endswith(".docx"):
                    raise ApiProblem("invalid_input", "ZIP entries must be DOCX documents")
                name = submissionName(name)
                if len(documents) >= MAX_DOCX_FILES:
                    raise ApiProblem("file_too_large", "ZIP contains too many documents", status=413)
                limit = min(remaining, MAX_DOCX_BYTES)
                with archive.open(member) as source:
                    content = source.read(limit + 1)
                if len(content) > limit:
                    raise ApiProblem("file_too_large", "ZIP exceeds the unpacked size limit", status=413)
                validateDocx(content)
                documents.append(SubmissionDocument(name, content))
                remaining -= len(content)
    except (BadZipFile, RuntimeError, OSError, ValueError, EOFError, ZlibError) as error:
        raise ApiProblem("invalid_input", "Expected a valid ZIP archive") from error

    if not documents:
        raise ApiProblem("invalid_input", "ZIP must contain at least one DOCX document")
    return documents


def storeUpload(
    db: Session,
    settings: Settings,
    assignmentId: str,
    assignmentRev: int,
    documents: list[SubmissionDocument],
) -> UploadAccepted:
    uploadId = str(uuid.uuid4())
    records = []
    with closing(buildS3Client(settings)) as client:
        for document in documents:
            fileId = str(uuid.uuid4())
            docRef = storeDocx(client, f"uploads/{uploadId}/{fileId}.docx", document.content)
            records.append(UploadFileRecord(
                fileId=fileId, uploadId=uploadId, originalName=document.originalName,
                bucket=docRef.bucket, objectKey=docRef.key, sha256=docRef.sha256,
            ))
    db.add(UploadRecord(uploadId=uploadId, assignmentId=assignmentId, assignmentRev=assignmentRev))
    db.flush()
    db.add_all(records)
    return UploadAccepted(uploadId=uploadId)
