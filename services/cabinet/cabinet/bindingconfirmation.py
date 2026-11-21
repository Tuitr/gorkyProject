"""Confirm a teacher's choice for one document or a group of photo pages."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet.classes import StudentRecord, activeStudents, lockStudentCreation
from cabinet.outbox import EventContext
from cabinet.uploads import INVALID_PAGE_ORDER, UploadFileRecord, finishGroupBinding, requireOwnedUpload, visibleUploadFiles
from contracts.v1.cabinet.bindings import BindingConfirmed, BindingRequest, SelectStudentBinding
from shared.httperrors import ApiProblem


def requireStudentInClass(db: Session, studentId: str, classId: str, *, alreadyBound: bool) -> StudentRecord:
    if len(studentId) == 36 and "\x00" not in studentId:
        query = select(StudentRecord).where(StudentRecord.classId == classId) if alreadyBound else activeStudents(classId)
        student = db.scalar(query.where(StudentRecord.studentId == studentId).with_for_update(read=True))
        if student is not None:
            return student
    raise ApiProblem("not_found", "Student not found in the class")


def confirmBinding(
    db: Session, uploadId: str, teacherId: str, request: BindingRequest, eventContext: EventContext,
) -> BindingConfirmed:
    upload, classId = requireOwnedUpload(db, uploadId, teacherId)
    if len(request.fileId) != 36 or "\x00" in request.fileId:
        raise ApiProblem("not_found", "Upload file not found")
    visible = visibleUploadFiles(uploadId)
    file = db.scalar(visible.where(UploadFileRecord.fileId == request.fileId))
    if file is None:
        raise ApiProblem("not_found", "Upload file not found")
    groupFilter = (
        UploadFileRecord.groupId == file.groupId if file.groupId is not None else UploadFileRecord.fileId == file.fileId
    )
    files = db.scalars(
        visible.where(groupFilter)
        .order_by(UploadFileRecord.pageIndex, UploadFileRecord.fileId)
        .with_for_update().execution_options(populate_existing=True)
    ).all()
    file = next((page for page in files if page.fileId == request.fileId), None)
    if file is None:
        raise ApiProblem("not_found", "Upload file not found")
    if file.status == "rejected":
        raise ApiProblem("conflict", "Rejected files cannot be bound")
    if file.groupId is not None and file.pageIndex is None:
        raise ApiProblem("conflict", INVALID_PAGE_ORDER)

    confirmed = file.bindingMode is not None or file.status == "bound"
    if isinstance(request, SelectStudentBinding):
        student = requireStudentInClass(
            db, request.studentId, classId, alreadyBound=confirmed and file.studentId == request.studentId,
        )
        if confirmed and file.studentId != student.studentId:
            raise ApiProblem("conflict", "A different student is already confirmed for this file")
        mode = "selected"
    elif confirmed:
        if file.bindingMode != "created" or file.studentId is None:
            raise ApiProblem("conflict", "A student is already confirmed for this file")
        student = requireStudentInClass(db, file.studentId, classId, alreadyBound=True)
        if student.name != request.newStudent.name:
            raise ApiProblem("conflict", "A student with a different name was created for this file")
        mode = "created"
    else:
        lockStudentCreation(db, classId)
        student = StudentRecord(studentId=str(uuid.uuid4()), classId=classId, name=request.newStudent.name)
        db.add(student)
        # Save the new student before adding the foreign key from the upload file.
        db.flush()
        mode = "created"

    if not confirmed:
        for page in files:
            page.studentId = student.studentId
            page.status = "matched"
            page.bindingMode = mode
            page.reason = None
            page.candidateStudentIds = [student.studentId]

    attemptId, checkId = finishGroupBinding(db, upload, files, eventContext)
    return BindingConfirmed(
        fileId=file.fileId,
        studentId=student.studentId,
        status="bound",
        attemptId=attemptId,
        checkId=checkId,
    )
