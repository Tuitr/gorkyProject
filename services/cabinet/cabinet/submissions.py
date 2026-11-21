"""Read the cabinet's submission identities and original photos."""

from collections.abc import Sequence

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from cabinet.attempts import AttemptRecord, CheckRecord
from cabinet.uploads import UploadFileRecord, UploadRecord
from contracts.v1.cabinet.submissions import AssignmentSubmissions, SubmissionSummary
from contracts.v1.grading.assessment import PhotoPage
from shared.submissionmedia import photoMediaType


def readPhotoPages(db: Session, checkIds: Sequence[str]) -> dict[str, list[PhotoPage]]:
    if not checkIds:
        return {}
    rows = db.execute(
        select(
            UploadFileRecord.checkId, UploadFileRecord.pageIndex, UploadFileRecord.uploadId,
            UploadFileRecord.fileId, UploadFileRecord.objectKey,
        )
        .join(UploadRecord, UploadRecord.uploadId == UploadFileRecord.uploadId)
        .where(UploadFileRecord.checkId.in_(checkIds))
        .order_by(UploadRecord.uploadedAt.asc().nulls_last(), UploadFileRecord.uploadId, UploadFileRecord.fileId)
    )
    pages: dict[str, dict[int, PhotoPage]] = {}
    for checkId, pageIndex, uploadId, fileId, key in rows:
        if checkId is None or photoMediaType(key) is None:
            continue
        index = pageIndex if pageIndex is not None else 0
        # Repeated imports may point to the same check and content. Take the earliest copy of each page.
        byIndex = pages.setdefault(checkId, {})
        if index not in byIndex:
            byIndex[index] = PhotoPage(pageIndex=index, url=f"/v1/uploads/{uploadId}/files/{fileId}/photo")
    return {checkId: [page for _, page in sorted(byIndex.items())] for checkId, byIndex in pages.items()}


def readLatestSubmissions(db: Session, assignmentId: str, assignmentRev: int) -> AssignmentSubmissions:
    uploadedAt = (
        select(func.min(UploadRecord.uploadedAt))
        .join(UploadFileRecord, UploadFileRecord.uploadId == UploadRecord.uploadId)
        .where(UploadFileRecord.checkId == CheckRecord.checkId)
        .correlate(CheckRecord)
        .scalar_subquery()
    )
    submissions = db.execute(
        select(AttemptRecord.studentId, AttemptRecord.attemptId, CheckRecord.checkId)
        .join(CheckRecord, CheckRecord.attemptId == AttemptRecord.attemptId)
        .where(
            CheckRecord.assignmentId == assignmentId, CheckRecord.assignmentRev == assignmentRev,
            AttemptRecord.deletedAt.is_(None),
        )
        .ext(distinct_on(AttemptRecord.studentId))
        .order_by(AttemptRecord.studentId, func.coalesce(uploadedAt, AttemptRecord.submittedAt).desc(),
                  AttemptRecord.attemptId.desc())
    ).all()
    pages = readPhotoPages(db, [row.checkId for row in submissions])
    return AssignmentSubmissions(
        assignmentId=assignmentId, assignmentRev=assignmentRev,
        submissions=[
            SubmissionSummary(studentId=studentId, attemptId=attemptId, checkId=checkId, pageCount=len(pages.get(checkId, [])))
            for studentId, attemptId, checkId in submissions
        ],
    )
