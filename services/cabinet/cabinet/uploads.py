"""Uploads and the original student documents they contain."""

import stat
import uuid
from collections.abc import Sequence
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from os import SEEK_END
from pathlib import PurePosixPath
from zipfile import ZIP_DEFLATED, ZIP_STORED, BadZipFile, ZipFile, ZipInfo
from zlib import error as ZlibError

from fastapi import UploadFile

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Select, String, UniqueConstraint, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.assignments import AssignmentRecord
from cabinet.attempts import AttemptRecord, ensureAttempt, ensureCheck
from cabinet.bindings import PhotoPageName, matchStudent, nameKey, photoPageName, rosterMatch, signatureNames
from cabinet.classes import ClassRecord, StudentRecord, activeStudents, lockStudentCreation
from cabinet.database import Base
from cabinet.documents import (
    DOCX_MEDIA_TYPE, MAX_DOCX_BYTES, buildS3Client, loadOriginal, readDocxBytes, readPhotoBytes,
    storeOriginal, validateDocx, validatePhoto,
)
from cabinet.outbox import EventContext
from cabinet.settings import Settings
from contracts.v1.cabinet.classes import StudentSummary
from contracts.v1.cabinet.uploads import (
    AssignmentUploads, UploadAccepted, UploadFileProgress, UploadFileStatus, UploadProgress,
)
from contracts.v1.events.checks import DocRef
from shared.httperrors import ApiProblem
from shared.submissionmedia import MAX_SOURCE_BYTES, photoMediaType


MAX_ZIP_BYTES = 50 * 1024 * 1024
MAX_UNPACKED_BYTES = 100 * 1024 * 1024
MAX_DOCX_FILES = 100
MAX_PHOTO_FILES = 100
MAX_ZIP_ENTRIES = 200
INVALID_PAGE_ORDER = "Пронумеруйте все листы по порядку: __01, __02 и далее. Загрузите комплект заново."


@dataclass(frozen=True)
class SubmissionDocument:
    originalName: str
    content: bytes
    signatures: tuple[str, ...]
    mediaType: str = DOCX_MEDIA_TYPE

    @property
    def extension(self) -> str:
        return ".docx" if self.mediaType == DOCX_MEDIA_TYPE else PurePosixPath(self.originalName).suffix.lower()


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
    uploadedAt: Mapped[datetime | None] = mapped_column("uploaded_at", DateTime(timezone=True))


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
    attemptId: Mapped[str | None] = mapped_column(
        "attempt_id", ForeignKey("cabinet.attempts.attempt_id", name="fk_upload_files_attempt")
    )
    checkId: Mapped[str | None] = mapped_column(
        "check_id", ForeignKey("cabinet.attempt_checks.check_id", name="fk_upload_files_check"), index=True,
    )
    bindingMode: Mapped[str | None] = mapped_column("binding_mode", String(16))
    groupId: Mapped[str | None] = mapped_column("group_id", String(36))
    pageIndex: Mapped[int | None] = mapped_column("page_index")


def submissionName(filename: str) -> str:
    if not filename.strip() or len(filename) > 255 or "\x00" in filename:
        raise ApiProblem("invalid_input", "Expected a document filename of at most 255 characters")
    return filename


def uploadFilename(upload: UploadFile) -> str:
    return (upload.filename or "").replace("\\", "/").rsplit("/", 1)[-1]


def readSubmission(upload: UploadFile) -> list[SubmissionDocument]:
    filename = uploadFilename(upload)
    if filename.lower().endswith(".zip"):
        return readSubmissionZip(upload)
    if (media := photoMediaType(filename)) is not None:
        original = readPhotoBytes(upload, media)
        return [SubmissionDocument(submissionName(filename), original, (), media)]
    original = readDocxBytes(upload)
    return [SubmissionDocument(submissionName(filename), original, signatureNames(validateDocx(original)))]


def readSubmissionFiles(uploads: Sequence[UploadFile]) -> list[SubmissionDocument]:
    if len(uploads) == 1:
        return readSubmission(uploads[0])
    if len(uploads) > MAX_PHOTO_FILES:
        raise ApiProblem("file_too_large", "Too many photos in one upload", status=413)
    documents = []
    remaining = MAX_UNPACKED_BYTES
    for upload in uploads:
        filename = uploadFilename(upload)
        media = photoMediaType(filename)
        if media is None:
            raise ApiProblem("invalid_input", "Select only JPEG, PNG or WebP when uploading multiple files")
        original = readPhotoBytes(upload, media)
        remaining -= len(original)
        if remaining < 0:
            raise ApiProblem("file_too_large", "Photos exceed the total size limit", status=413)
        documents.append(SubmissionDocument(submissionName(filename), original, (), media))
    return documents


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
                media = photoMediaType(name)
                if media is None and not name.lower().endswith(".docx"):
                    raise ApiProblem("invalid_input", "ZIP entries must be DOCX or JPEG, PNG and WebP photos")
                if documents and (documents[0].mediaType == DOCX_MEDIA_TYPE) != (media is None):
                    raise ApiProblem("invalid_input", "Upload photos and DOCX in separate archives")
                name = submissionName(name)
                fileLimit = MAX_PHOTO_FILES if media is not None else MAX_DOCX_FILES
                if len(documents) >= fileLimit:
                    raise ApiProblem("file_too_large", "ZIP contains too many documents", status=413)
                limit = min(remaining, MAX_SOURCE_BYTES if media is not None else MAX_DOCX_BYTES)
                with archive.open(member) as source:
                    content = source.read(limit + 1)
                if len(content) > limit:
                    raise ApiProblem("file_too_large", "ZIP exceeds the unpacked size limit", status=413)
                if media is None:
                    documents.append(SubmissionDocument(name, content, signatureNames(validateDocx(content))))
                else:
                    validatePhoto(content, media)
                    documents.append(SubmissionDocument(name, content, (), media))
                remaining -= len(content)
    except (BadZipFile, RuntimeError, OSError, ValueError, EOFError, ZlibError) as error:
        raise ApiProblem("invalid_input", "Expected a valid ZIP archive") from error

    if not documents:
        raise ApiProblem("invalid_input", "ZIP must contain at least one supported file")
    return documents


def lockUploadStorage(db: Session, uploadId: str) -> None:
    """Hold the same lock while writing or cleaning an upload's S3 prefix."""
    db.execute(select(func.pg_advisory_xact_lock(func.hashtextextended(f"cabinet.uploads/{uploadId}", 0))))


def storeUpload(
    db: Session,
    settings: Settings,
    assignmentId: str,
    assignmentRev: int,
    classId: str,
    documents: list[SubmissionDocument],
    eventContext: EventContext,
    *,
    uploadId: str | None = None,
) -> UploadAccepted:
    uploadId = uploadId if uploadId is not None else str(uuid.uuid4())
    lockUploadStorage(db, uploadId)
    records = []
    with closing(buildS3Client(settings)) as client:
        for document in documents:
            fileId = str(uuid.uuid4())
            docRef = storeOriginal(client, f"uploads/{uploadId}/{fileId}{document.extension}", document.content, document.mediaType)
            file = UploadFileRecord(
                fileId=fileId, uploadId=uploadId, originalName=document.originalName,
                bucket=docRef.bucket, objectKey=docRef.key, sha256=docRef.sha256,
            )
            records.append(file)
    upload = UploadRecord(
        uploadId=uploadId, assignmentId=assignmentId, assignmentRev=assignmentRev, uploadedAt=eventContext.occurredAt,
    )
    db.add(upload)
    db.flush()
    if documents[0].mediaType == DOCX_MEDIA_TYPE:
        roster = db.scalars(activeStudents(classId)).all()
        for file, document in zip(records, documents, strict=True):
            applyMatch(file, document.signatures, roster)
    else:
        matchPhotoGroups(db, classId, records)
    finishMatchedFiles(db, upload, records, eventContext)
    db.add_all(records)
    return UploadAccepted(uploadId=uploadId)


def applyMatch(file: UploadFileRecord, signatures: tuple[str, ...], roster: Sequence[StudentRecord]) -> None:
    match = matchStudent(file.originalName, signatures, roster)
    file.status = "matched" if match.studentId is not None else "needs_confirmation"
    file.studentId = match.studentId
    file.candidateStudentIds = match.candidateIds
    file.reason = match.reason


def matchPhotoGroups(db: Session, classId: str, files: Sequence[UploadFileRecord]) -> None:
    lockStudentCreation(db, classId)
    roster = list(db.scalars(activeStudents(classId)))
    groups: dict[tuple[str, ...], list[tuple[UploadFileRecord, PhotoPageName]]] = {}
    for file in files:
        page = photoPageName(file.originalName)
        groups.setdefault(page.groupKey, []).append((file, page))

    for group in groups.values():
        groupId = str(uuid.uuid4())
        numbers = {page.number for _, page in group}
        singleton = len(group) == 1 and numbers == {None}
        if not singleton and numbers != set(range(1, len(group) + 1)):
            for file, _ in group:
                file.groupId = groupId
                file.status = "needs_confirmation"
                file.candidateStudentIds = []
                file.reason = INVALID_PAGE_ORDER
            continue

        first = min((page for _, page in group), key=lambda page: page.number or 0)
        key = first.groupKey if first.studentName is not None else nameKey(first.groupKey[0])
        candidates = rosterMatch(key, roster)
        studentId = None
        if first.studentName is not None and len(candidates.exact) == 1:
            studentId = next(iter(candidates.exact))
        elif first.studentName is not None and not candidates.exact:
            studentId = str(uuid.uuid4())
            student = StudentRecord(studentId=studentId, classId=classId, name=first.studentName)
            db.add(student)
            roster.append(student)

        candidateIds = [studentId] if studentId is not None else sorted(candidates.candidates)
        for file, page in group:
            file.groupId = groupId
            file.pageIndex = (page.number or 1) - 1
            file.status = "matched" if studentId is not None else "needs_confirmation"
            file.studentId = studentId
            file.candidateStudentIds = candidateIds
            if studentId is None:
                file.reason = "Укажите ученика для этой работы."


def finishGroupBinding(
    db: Session, upload: UploadRecord, files: Sequence[UploadFileRecord], eventContext: EventContext,
) -> tuple[str, str]:
    file = files[0]
    if file.status == "bound":
        assert file.attemptId is not None and file.checkId is not None
        return file.attemptId, file.checkId

    assert file.status == "matched" and file.studentId is not None
    refs = [DocRef(bucket=page.bucket, key=page.objectKey, sha256=page.sha256) for page in files]
    ref = refs[0]
    pageRefs = refs if len(refs) > 1 else None
    attemptId = ensureAttempt(
        db, upload.assignmentId, file.studentId, ref, upload.uploadedAt or eventContext.occurredAt, pageRefs=pageRefs,
    )
    checkId = ensureCheck(
        db, attemptId, upload.assignmentId, upload.assignmentRev, file.studentId, ref, eventContext, pageRefs=pageRefs,
    )
    for page in files:
        page.attemptId = attemptId
        page.checkId = checkId
        page.status = "bound"
    return attemptId, checkId


def finishMatchedFiles(
    db: Session, upload: UploadRecord, files: Sequence[UploadFileRecord], eventContext: EventContext,
) -> None:
    # Concurrent archives may contain the same works in different orders.
    groups: dict[str, list[UploadFileRecord]] = {}
    for file in files:
        if file.status == "matched":
            groups.setdefault(file.groupId or file.fileId, []).append(file)
    if not groups:
        return
    for group in groups.values():
        group.sort(key=lambda file: file.pageIndex or 0)
    matchedIds = {group[0].studentId for group in groups.values()}
    activeIds = set(db.scalars(
        select(StudentRecord.studentId).where(
            StudentRecord.studentId.in_(matchedIds), StudentRecord.deletedAt.is_(None),
        ).with_for_update(read=True)
    ))
    for group in sorted(groups.values(), key=lambda pages: (pages[0].studentId or "", tuple(page.sha256 for page in pages))):
        if group[0].studentId in activeIds:
            finishGroupBinding(db, upload, group, eventContext)
        else:
            for file in group:
                file.studentId = None
                file.bindingMode = None
                file.status = "needs_confirmation"
                file.reason = "Ученик удалён из класса. Выберите другого ученика."


def matchPendingFiles(
    db: Session, settings: Settings, classId: str, files: Sequence[UploadFileRecord],
) -> None:
    roster = db.scalars(activeStudents(classId)).all()
    with closing(buildS3Client(settings)) as client:
        for file in files:
            if file.status != "pending_binding":
                continue
            try:
                original = loadOriginal(client, DocRef(bucket=file.bucket, key=file.objectKey, sha256=file.sha256))
            except ApiProblem:
                # Leave the file pending so the next poll can retry a storage failure.
                return
            try:
                signatures = () if photoMediaType(file.objectKey) else signatureNames(validateDocx(original))
            except ApiProblem:
                file.status = "rejected"
                file.reason = "Не удалось прочитать документ."
            else:
                applyMatch(file, signatures, roster)


def requireOwnedUpload(db: Session, uploadId: str, teacherId: str) -> tuple[UploadRecord, str]:
    if len(uploadId) != 36 or "\x00" in uploadId:
        raise ApiProblem("not_found", "Upload not found")
    owned = db.execute(
        select(UploadRecord, AssignmentRecord.classId)
        .join(
            AssignmentRecord,
            (AssignmentRecord.assignmentId == UploadRecord.assignmentId)
            & (AssignmentRecord.assignmentRev == UploadRecord.assignmentRev),
        )
        .join(ClassRecord, ClassRecord.classId == AssignmentRecord.classId)
        .where(
            UploadRecord.uploadId == uploadId, ClassRecord.teacherId == teacherId,
            ClassRecord.deletedAt.is_(None), AssignmentRecord.deletedAt.is_(None),
        )
        .with_for_update(read=True, of=(ClassRecord, AssignmentRecord))
    ).one_or_none()
    if owned is None:
        raise ApiProblem("not_found", "Upload not found")
    upload, classId = owned
    return upload, classId


def visibleUploadFiles(uploadId: str) -> Select[UploadFileRecord]:
    archived = select(AttemptRecord.attemptId).where(
        AttemptRecord.attemptId == UploadFileRecord.attemptId, AttemptRecord.deletedAt.is_not(None),
    ).exists()
    return select(UploadFileRecord).where(UploadFileRecord.uploadId == uploadId, ~archived)


def listActiveUploads(db: Session, assignmentId: str, assignmentRev: int) -> AssignmentUploads:
    unresolvedFile = (
        select(UploadFileRecord.fileId)
        .where(
            UploadFileRecord.uploadId == UploadRecord.uploadId,
            UploadFileRecord.status.in_(("pending_binding", "needs_confirmation", "matched")),
        )
        .exists()
    )
    uploads = db.scalars(
        select(UploadRecord)
        .where(
            UploadRecord.assignmentId == assignmentId, UploadRecord.assignmentRev == assignmentRev, unresolvedFile,
        )
        .order_by(UploadRecord.uploadedAt, UploadRecord.uploadId)
    )
    return AssignmentUploads(
        assignmentId=assignmentId, assignmentRev=assignmentRev,
        uploads=[UploadAccepted(uploadId=upload.uploadId) for upload in uploads],
    )


def readUploadPhoto(db: Session, settings: Settings, uploadId: str, fileId: str, teacherId: str) -> tuple[bytes, str]:
    requireOwnedUpload(db, uploadId, teacherId)
    if len(fileId) != 36 or "\x00" in fileId:
        raise ApiProblem("not_found", "Photo not found")
    file = db.scalar(visibleUploadFiles(uploadId).where(UploadFileRecord.fileId == fileId))
    if file is None or (media := photoMediaType(file.objectKey)) is None:
        raise ApiProblem("not_found", "Photo not found")
    with closing(buildS3Client(settings)) as client:
        original = loadOriginal(client, DocRef(bucket=file.bucket, key=file.objectKey, sha256=file.sha256))
    return original, media


def getUploadProgress(
    db: Session, settings: Settings, uploadId: str, teacherId: str, eventContext: EventContext,
) -> UploadProgress:
    upload, classId = requireOwnedUpload(db, uploadId, teacherId)
    fileQuery = (
        visibleUploadFiles(uploadId)
        .order_by(UploadFileRecord.groupId.nulls_last(), UploadFileRecord.pageIndex.nulls_last(),
                  UploadFileRecord.originalName, UploadFileRecord.fileId)
    )
    files = db.scalars(fileQuery).all()
    if any(file.status in {"pending_binding", "matched"} for file in files):
        # Complete older uploads on first read, with the same file locks as manual confirmation.
        files = db.scalars(fileQuery.with_for_update().execution_options(populate_existing=True)).all()
        if any(file.status == "pending_binding" for file in files):
            matchPendingFiles(db, settings, classId, files)
        finishMatchedFiles(db, upload, files, eventContext)

    candidateIds = {studentId for file in files for studentId in file.candidateStudentIds}
    candidates = {}
    if candidateIds:
        students = db.scalars(activeStudents(classId).where(StudentRecord.studentId.in_(candidateIds)))
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
                isPhoto=True if photoMediaType(file.objectKey) else None,
                groupId=file.groupId,
                pageIndex=file.pageIndex,
                reasonCode="invalid_page_order" if file.groupId is not None and file.pageIndex is None else None,
            )
            for file in files
        ],
    )
