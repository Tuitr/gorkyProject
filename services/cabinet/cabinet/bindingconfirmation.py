"""Confirm a teacher's choice for exactly one uploaded document."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from cabinet.classes import StudentRecord, activeStudents
from cabinet.outbox import EventContext
from cabinet.uploads import UploadFileRecord, finishFileBinding, requireOwnedUpload, visibleUploadFiles
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
    file = db.scalar(
        visibleUploadFiles(uploadId)
        .where(UploadFileRecord.fileId == request.fileId)
        .with_for_update()
    )
    if file is None:
        raise ApiProblem("not_found", "Upload file not found")
    if file.status == "rejected":
        raise ApiProblem("conflict", "Rejected files cannot be bound")

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
        student = StudentRecord(studentId=str(uuid.uuid4()), classId=classId, name=request.newStudent.name)
        db.add(student)
        # Save the new student before adding the foreign key from the upload file.
        db.flush()
        mode = "created"

    if not confirmed:
        file.studentId = student.studentId
        file.status = "matched"
        file.bindingMode = mode
        file.reason = None
        file.candidateStudentIds = [student.studentId]

    attemptId, checkId = finishFileBinding(db, upload, file, eventContext)
    return BindingConfirmed(
        fileId=file.fileId,
        studentId=student.studentId,
        status="bound",
        attemptId=attemptId,
        checkId=checkId,
    )
