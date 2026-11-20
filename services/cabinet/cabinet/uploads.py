"""Uploads and the original student documents they contain."""

import stat
import uuid
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from os import SEEK_END
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo
from zlib import error as ZlibError

from fastapi import UploadFile

from sqlalchemy import CheckConstraint, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.assignments import AssignmentRecord
from cabinet.bindings import matchStudent, signatureNames
from cabinet.classes import ClassRecord, StudentRecord
from cabinet.database import Base
from cabinet.documents import MAX_DOCX_BYTES, buildS3Client, loadDocx, readDocxBytes, storeDocx, validateDocx
from cabinet.settings import Settings
from contracts.v1.cabinet.classes import StudentSummary
from contracts.v1.cabinet.uploads import UploadAccepted, UploadFileProgress, UploadFileStatus, UploadProgress
from contracts.v1.events.checks import DocRef
from shared.httperrors import ApiProblem


MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_DOCX_FILES = 100
MAX_ZIP_ENTRIES = 200


@dataclass(frozen=True)
class SubmissionDocument:
    originalName: str
    content: bytes
    signatures: tuple[str, ...]


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
    __table_args__ = (
        UniqueConstraint("object_key"),
        CheckConstraint(
            "status IN ('pending_binding', 'needs_confirmation', 'matched', 'bound', 'rejected')",
            name="ck_upload_files_status",
        ),
        CheckConstraint("status <> 'matched' OR student_id IS NOT NULL", name="ck_upload_files_match"),
        CheckConstraint(
            "binding_mode IS NULL OR (binding_mode IN ('selected', 'created') AND student_id IS NOT NULL)",
            name="ck_upload_files_binding_mode",
        ),
        CheckConstraint(
            "(status = 'bound' AND student_id IS NOT NULL AND attempt_id IS NOT NULL AND check_id IS NOT NULL) "
            "OR (status <> 'bound' AND attempt_id IS NULL AND check_id IS NULL)",
            name="ck_upload_files_attempt",
        ),
    )

    fileId: Mapped[str] = mapped_column("file_id", String(36), primary_key=True)
    uploadId: Mapped[str] = mapped_column(
        "upload_id", ForeignKey("cabinet.uploads.upload_id", ondelete="CASCADE"), index=True
    )
    originalName: Mapped[str] = mapped_column("original_name", String(255))
    bucket: Mapped[str] = mapped_column(String(63))
    objectKey: Mapped[str] = mapped_column("object_key", String(255))
    sha256: Mapped[str] = mapped_column(String(64))
    status: Mapped[UploadFileStatus] = mapped_column(String(32), server_default="pending_binding")
    candidateStudentIds: Mapped[list[str]] = mapped_column("candidate_student_ids", ARRAY(String(36)), server_default="{}")
    reason: Mapped[str | None] = mapped_column(String(255))
    studentId: Mapped[str | None] = mapped_column("student_id", ForeignKey("cabinet.students.student_id"))
    attemptId: Mapped[str | None] = mapped_column("attempt_id", String(36))
    checkId: Mapped[str | None] = mapped_column("check_id", String(36))
    bindingMode: Mapped[str | None] = mapped_column("binding_mode", String(16))


def submissionName(filename: str) -> str:
    if not filename.strip() or len(filename) > 255 or "\x00" in filename:
        raise ApiProblem("invalid_input", "Expected a document filename of at most 255 characters")
    return filename


def readSubmission(upload: UploadFile) -> list[SubmissionDocument]:
    filename = (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    if filename.lower().endswith(".zip"):
        return readSubmissionZip(upload)
    original = readDocxBytes(upload)
    return [SubmissionDocument(submissionName(filename), original, signatureNames(validateDocx(original)))]


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
                documents.append(SubmissionDocument(name, content, signatureNames(validateDocx(content))))
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
    classId: str,
    documents: list[SubmissionDocument],
) -> UploadAccepted:
    uploadId = str(uuid.uuid4())
    records = []
    roster = db.scalars(select(StudentRecord).where(StudentRecord.classId == classId)).all()
    with closing(buildS3Client(settings)) as client:
        for document in documents:
            fileId = str(uuid.uuid4())
            docRef = storeDocx(client, f"uploads/{uploadId}/{fileId}.docx", document.content)
            file = UploadFileRecord(
                fileId=fileId, uploadId=uploadId, originalName=document.originalName,
                bucket=docRef.bucket, objectKey=docRef.key, sha256=docRef.sha256,
            )
            applyMatch(file, document.signatures, roster)
            records.append(file)
    db.add(UploadRecord(uploadId=uploadId, assignmentId=assignmentId, assignmentRev=assignmentRev))
    db.flush()
    db.add_all(records)
    return UploadAccepted(uploadId=uploadId)


def applyMatch(file: UploadFileRecord, signatures: tuple[str, ...], roster: Sequence[StudentRecord]) -> None:
    match = matchStudent(file.originalName, signatures, roster)
    file.status = "matched" if match.studentId is not None else "needs_confirmation"
    file.studentId = match.studentId
    file.candidateStudentIds = match.candidateIds
    file.reason = match.reason


def matchPendingFiles(
    db: Session, settings: Settings, classId: str, files: Sequence[UploadFileRecord],
) -> None:
    roster = db.scalars(select(StudentRecord).where(StudentRecord.classId == classId)).all()
    with closing(buildS3Client(settings)) as client:
        for file in files:
            if file.status != "pending_binding":
                continue
            try:
                original = loadDocx(client, DocRef(bucket=file.bucket, key=file.objectKey, sha256=file.sha256))
            except ApiProblem:
                # Leave the file pending so the next poll can retry a storage failure.
                return
            try:
                signatures = signatureNames(validateDocx(original))
            except ApiProblem:
                file.status = "rejected"
                file.reason = "Не удалось прочитать документ."
            else:
                applyMatch(file, signatures, roster)


def requireOwnedUploadClass(db: Session, uploadId: str, teacherId: str) -> str:
    if len(uploadId) != 36 or "\x00" in uploadId:
        raise ApiProblem("not_found", "Upload not found")
    classId = db.scalar(
        select(AssignmentRecord.classId)
        .select_from(UploadRecord)
        .join(
            AssignmentRecord,
            (AssignmentRecord.assignmentId == UploadRecord.assignmentId)
            & (AssignmentRecord.assignmentRev == UploadRecord.assignmentRev),
        )
        .join(ClassRecord, ClassRecord.classId == AssignmentRecord.classId)
        .where(UploadRecord.uploadId == uploadId, ClassRecord.teacherId == teacherId)
    )
    if classId is None:
        raise ApiProblem("not_found", "Upload not found")
    return classId


def getUploadProgress(db: Session, settings: Settings, uploadId: str, teacherId: str) -> UploadProgress:
    classId = requireOwnedUploadClass(db, uploadId, teacherId)
    fileQuery = (
        select(UploadFileRecord)
        .where(UploadFileRecord.uploadId == uploadId)
        .order_by(UploadFileRecord.originalName, UploadFileRecord.fileId)
    )
    files = db.scalars(fileQuery).all()
    if any(file.status == "pending_binding" for file in files):
        # Older uploads may not have been matched yet. Serialize the update for this upload.
        files = db.scalars(fileQuery.with_for_update().execution_options(populate_existing=True)).all()
        matchPendingFiles(db, settings, classId, files)

    candidateIds = {studentId for file in files for studentId in file.candidateStudentIds}
    candidates = {}
    if candidateIds:
        students = db.scalars(
            select(StudentRecord).where(StudentRecord.classId == classId, StudentRecord.studentId.in_(candidateIds))
        )
        candidates = {
            student.studentId: StudentSummary(studentId=student.studentId, name=student.name) for student in students
        }

    return UploadProgress(
        uploadId=uploadId,
        files=[
            UploadFileProgress(
                fileId=file.fileId,
                originalName=file.originalName,
                status=file.status,
                candidates=[candidates[studentId] for studentId in file.candidateStudentIds if studentId in candidates],
                reason=file.reason,
                studentId=file.studentId if file.status in {"matched", "bound"} else None,
                attemptId=file.attemptId,
                checkId=file.checkId,
            )
            for file in files
        ],
    )
