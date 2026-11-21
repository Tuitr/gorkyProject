"""FastAPI app of the teacher cabinet: uv run uvicorn cabinet.main:createApp --factory

Public routes live under /v1. The schema is served at /openapi.json.
"""

from datetime import date, timedelta
import hashlib
import secrets
import uuid
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, FastAPI, File, Form, Query, Request, Response, UploadFile
from pydantic import Json
from starlette.datastructures import UploadFile as MultipartFile
from sqlalchemy import create_engine, delete, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cabinet.assignments import (
    AssignmentRecord, deleteAssignment, listActiveAssignments, requireOwnedAssignment, storeAssignment,
)
from cabinet.attempts import CheckRecord, deleteAttempt
from cabinet.auth.passwords import Passwords
from cabinet.auth.service import TeacherAccounts
from cabinet.auth.tables import LoginThrottleRecord, SessionRecord, TeacherRecord
from cabinet.auth.throttle import LoginThrottle
from cabinet.bindingconfirmation import confirmBinding
from cabinet.checks import readGradingCheck, requireOwnedCheck
from cabinet.classes import ClassRecord, StudentRecord, activeStudents, deleteClass, deleteStudent, requireOwnedClass
from cabinet.cleanupuploads import tryCleanUpload
from cabinet.documents import readDocx, readPhotoBytes
from cabinet.gradingclient import invalidGradingResponse, readGradingModel, sendGradingRequest
from cabinet.outbox import EventContext, OutboxRecord
from cabinet.reports import combineReport, deletedReportCheckIds, readReportGrades, readReportRoster
from cabinet.reviews import gradingChanges, readMarkdown, reviewConflictResponse
from cabinet.settings import Settings
from cabinet.uploads import UploadFileRecord, UploadRecord, getUploadProgress, readSubmission, readUploadPhoto, storeUpload
from contracts.v1.cabinet.assignments import AssignmentSummary, AssignmentVersion
from contracts.v1.cabinet.bindings import BindingConfirmed, BindingRequest
from contracts.v1.cabinet.classes import ClassSummary, CreateClass, CreateStudent, StudentSummary
from contracts.v1.cabinet.grades import CheckGrade, SetGrade
from contracts.v1.cabinet.registration import TeacherRegistration
from contracts.v1.cabinet.reports import AssignmentReport
from contracts.v1.cabinet.reviews import EditReview, PublishReview, ReviewConflict, ReviewEdited, ReviewRevision
from contracts.v1.cabinet.sessions import LoginRequest, TeacherSession
from contracts.v1.cabinet.uploads import UploadAccepted, UploadProgress
from contracts.v1.common.errors import ApiError
from contracts.v1.events.checks import Subject, TaskPoints, TeacherRequirement
from contracts.v1.grading.assignmentreferences import AssignmentReferences
from contracts.v1.grading.assignmentreport import AssignmentReport as GradingReport
from contracts.v1.grading.checks import CheckReady, CheckStatus
from shared.clock import Clock, SystemClock
from shared.dotenv import loadDotEnv
from shared.httperrors import ApiProblem, getTraceId, installErrorHandlers
from shared.ocrconfig import parseOcrUrl
from shared.submissionmedia import photoMediaType

COOKIE = "__Host-cabinet"
SESSION_SECONDS = 8 * 3600


def checkOrigin(request: Request) -> None:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return
    origin = request.headers.get("origin")
    if request.headers.get("sec-fetch-site") == "cross-site" or (
        origin is not None and origin != str(request.base_url).rstrip("/")
    ):
        raise ApiProblem("forbidden", "Cross-origin request refused")


async def requireOneUploadFile(request: Request) -> None:
    form = await request.form()
    parts = [name for name, part in form.multi_items() if isinstance(part, MultipartFile)]
    if parts != ["file"]:
        raise ApiProblem("invalid_input", "Expected exactly one file")


def tokenHash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


UNKNOWN_CLIENT = "unknown"


def buildAccounts(
    settings: Settings, sessionFactory: sessionmaker[Session], passwords: Passwords, clock: Clock
) -> TeacherAccounts:
    throttle = LoginThrottle(
        sessionFactory,
        clock,
        maxFailures=settings.loginMaxFailures,
        clientMaxFailures=settings.clientMaxFailures,
        windowS=settings.loginWindowS,
    )
    return TeacherAccounts(sessionFactory, passwords, throttle)


def createApp(
    settings: Settings | None = None,
    *,
    sessionFactory: sessionmaker[Session] | None = None,
    passwords: Passwords | None = None,
    clock: Clock | None = None,
    gradingTransport: httpx.BaseTransport | None = None,
) -> FastAPI:
    if settings is None:
        loadDotEnv()
        settings = Settings.fromEnv()
    if sessionFactory is None:
        sessionFactory = sessionmaker(create_engine(settings.databaseUrl), expire_on_commit=False)
    sessionClock = clock or SystemClock()
    accounts = buildAccounts(
        settings, sessionFactory, passwords if passwords is not None else Passwords(), sessionClock
    )

    app = FastAPI(title="Teacher cabinet API", version="1.0.0")
    installErrorHandlers(app)
    app.state.teacherAccounts = accounts
    v1 = APIRouter(prefix="/v1", dependencies=[Depends(checkOrigin)])

    def eventContext(request: Request) -> EventContext:
        return EventContext(traceId=getTraceId(request), occurredAt=sessionClock.now())

    @app.get("/ready", include_in_schema=False)
    def readiness() -> dict[str, str]:
        try:
            with sessionFactory() as session:
                session.execute(select(TeacherRecord).limit(0))
                session.execute(select(LoginThrottleRecord).limit(0))
                session.execute(select(SessionRecord).limit(0))
                session.execute(select(ClassRecord).limit(0))
                session.execute(select(StudentRecord).limit(0))
                session.execute(select(AssignmentRecord).limit(0))
                session.execute(select(UploadRecord).limit(0))
                session.execute(select(UploadFileRecord).limit(0))
                session.execute(select(OutboxRecord).limit(0))
        except SQLAlchemyError as error:
            raise ApiProblem("unavailable", "Cabinet database is unavailable") from error
        return {"status": "ready"}

    @v1.post(
        "/teachers/register",
        status_code=201,
        responses={403: {"model": ApiError}, 409: {"model": ApiError}, 422: {"model": ApiError}, 500: {"model": ApiError}},
    )
    def registerTeacher(body: TeacherRegistration, response: Response) -> TeacherSession:
        teacher = accounts.create(body.email, body.displayName, body.password)
        response.headers["Cache-Control"] = "no-store"
        return teacher

    @v1.post(
        "/sessions",
        responses={
            401: {"model": ApiError, "description": "Wrong email or password"},
            403: {"model": ApiError, "description": "Cross-origin request refused"},
            422: {"model": ApiError, "description": "Invalid request"},
            429: {
                "model": ApiError,
                "description": "Too many login attempts",
                "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Delay in seconds"}},
            },
            500: {"model": ApiError, "description": "Internal error"},
        },
    )
    def createSession(body: LoginRequest, request: Request, response: Response) -> TeacherSession:
        clientAddress = request.client.host if request.client is not None else UNKNOWN_CLIENT
        teacher = accounts.authenticate(body, clientAddress)
        token = secrets.token_urlsafe(32)
        with sessionFactory.begin() as db:
            db.add(SessionRecord(tokenHash=tokenHash(token), teacherId=teacher.teacherId,
                                 expiresAt=sessionClock.now() + timedelta(seconds=SESSION_SECONDS)))
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, secure=True, httponly=True, samesite="strict")
        response.headers["Cache-Control"] = "no-store"
        return teacher

    def currentTeacher(request: Request, response: Response) -> TeacherSession:
        with sessionFactory() as db:
            teacher = db.scalar(select(TeacherRecord).join(SessionRecord).where(
                SessionRecord.tokenHash == tokenHash(request.cookies.get(COOKIE, "")),
                SessionRecord.expiresAt > sessionClock.now(),
            ))
            if teacher is None:
                raise ApiProblem("unauthorized", "Session is invalid or expired")
            response.headers["Cache-Control"] = "no-store"
            return TeacherSession(teacherId=teacher.teacherId, displayName=teacher.displayName)

    @v1.get("/classes", responses={401: {"model": ApiError}})
    def listClasses(teacher: TeacherSession = Depends(currentTeacher)) -> list[ClassSummary]:
        with sessionFactory() as db:
            rows = db.scalars(
                select(ClassRecord).where(ClassRecord.teacherId == teacher.teacherId, ClassRecord.deletedAt.is_(None))
                .order_by(ClassRecord.name, ClassRecord.classId)
            )
            return [ClassSummary(classId=row.classId, name=row.name) for row in rows]

    @v1.post(
        "/classes", status_code=201,
        responses={401: {"model": ApiError}, 403: {"model": ApiError}, 422: {"model": ApiError}},
    )
    def createClass(body: CreateClass, teacher: TeacherSession = Depends(currentTeacher)) -> ClassSummary:
        created = ClassSummary(classId=str(uuid.uuid4()), name=body.name)
        with sessionFactory.begin() as db:
            db.add(ClassRecord(classId=created.classId, teacherId=teacher.teacherId, name=created.name))
        return created

    @v1.delete(
        "/classes/{classId}", status_code=204,
        responses={401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError}},
    )
    def deleteTeacherClass(classId: str, teacher: TeacherSession = Depends(currentTeacher)) -> Response:
        with sessionFactory.begin() as db:
            deleteClass(db, classId, teacher.teacherId, sessionClock.now())
        return Response(status_code=204)

    @v1.get("/classes/{classId}/students", responses={401: {"model": ApiError}, 404: {"model": ApiError}})
    def listStudents(classId: str, teacher: TeacherSession = Depends(currentTeacher)) -> list[StudentSummary]:
        with sessionFactory() as db:
            requireOwnedClass(db, classId, teacher.teacherId)
            rows = db.scalars(
                activeStudents(classId)
                .order_by(StudentRecord.name, StudentRecord.studentId)
            )
            return [StudentSummary(studentId=row.studentId, name=row.name) for row in rows]

    @v1.post(
        "/classes/{classId}/students", status_code=201,
        responses={401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError}, 422: {"model": ApiError}},
    )
    def addStudent(
        classId: str, body: CreateStudent, teacher: TeacherSession = Depends(currentTeacher)
    ) -> StudentSummary:
        created = StudentSummary(studentId=str(uuid.uuid4()), name=body.name)
        with sessionFactory.begin() as db:
            requireOwnedClass(db, classId, teacher.teacherId, lockForWrite=True)
            db.add(StudentRecord(studentId=created.studentId, classId=classId, name=created.name))
        return created

    @v1.delete(
        "/classes/{classId}/students/{studentId}", status_code=204,
        responses={401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError}},
    )
    def deleteClassStudent(classId: str, studentId: str, teacher: TeacherSession = Depends(currentTeacher)) -> Response:
        with sessionFactory.begin() as db:
            deleteStudent(db, classId, studentId, teacher.teacherId, sessionClock.now())
        return Response(status_code=204)

    @v1.get("/classes/{classId}/assignments", responses={401: {"model": ApiError}, 404: {"model": ApiError}})
    def listClassAssignments(classId: str, teacher: TeacherSession = Depends(currentTeacher)) -> list[AssignmentSummary]:
        with sessionFactory() as db:
            requireOwnedClass(db, classId, teacher.teacherId)
            return listActiveAssignments(db, classId)

    @v1.post(
        "/classes/{classId}/assignments",
        status_code=201,
        dependencies=[Depends(requireOneUploadFile)],
        responses={
            401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError},
            413: {"model": ApiError}, 422: {"model": ApiError}, 503: {"model": ApiError},
        },
    )
    def uploadAssignment(
        classId: str,
        file: Annotated[UploadFile, File()],
        requirements: Annotated[Json[list[TeacherRequirement]], Form()],
        assignmentDate: Annotated[date | None, Form()] = None,
        assignmentId: Annotated[str | None, Form(min_length=36, max_length=36, pattern=r"^[0-9a-f-]+$")] = None,
        subject: Annotated[Subject | None, Form()] = None,
        title: Annotated[str | None, Form(max_length=120)] = None,
        taskPoints: Annotated[Json[TaskPoints | None], Form()] = None,
        teacher: TeacherSession = Depends(currentTeacher),
        event: EventContext = Depends(eventContext),
    ) -> AssignmentVersion:
        """Store one photo or DOCX with requirements and optional per-task maximum scores."""
        if title is not None and chr(0) in title:
            raise ApiProblem("invalid_input", "Title must not contain null characters")
        title = (title or "").strip() or None
        with sessionFactory.begin() as db:
            requireOwnedClass(db, classId, teacher.teacherId, lockForWrite=True)
            sourceName = file.filename or ""
            photoType = photoMediaType(sourceName)
            if photoType is not None and subject is None:
                raise ApiProblem("invalid_input", "Select a subject for a photo assignment")
            original = readPhotoBytes(file, photoType) if photoType is not None else readDocx(file)
            return storeAssignment(
                db, settings, classId, assignmentId, assignmentDate or event.occurredAt.date(),
                requirements, original, event, subject, title, sourceName=sourceName, taskPoints=taskPoints,
            )

    @v1.post(
        "/assignments/{assignmentId}/uploads",
        status_code=202,
        dependencies=[Depends(requireOneUploadFile)],
        responses={
            401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError},
            413: {"model": ApiError}, 422: {"model": ApiError}, 503: {"model": ApiError},
        },
    )
    def uploadSubmission(
        assignmentId: str,
        file: Annotated[UploadFile, File()],
        assignmentRev: Annotated[int, Form(ge=1, le=2_147_483_647)],
        teacher: TeacherSession = Depends(currentTeacher),
        event: EventContext = Depends(eventContext),
    ) -> UploadAccepted:
        uploadId = None
        try:
            with sessionFactory.begin() as db:
                classId = requireOwnedAssignment(db, assignmentId, assignmentRev, teacher.teacherId, lockForWrite=True)
                documents = readSubmission(file)
                uploadId = str(uuid.uuid4())
                return storeUpload(db, settings, assignmentId, assignmentRev, classId, documents, event, uploadId=uploadId)
        except Exception:
            if uploadId is not None:
                tryCleanUpload(sessionFactory, settings, uploadId)
            raise

    @v1.delete(
        "/assignments/{assignmentId}", status_code=204,
        responses={401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError}, 409: {"model": ApiError}},
    )
    def deleteTeacherAssignment(
        assignmentId: str, confirm: Annotated[bool, Query()] = False, teacher: TeacherSession = Depends(currentTeacher),
    ) -> Response:
        with sessionFactory.begin() as db:
            deleteAssignment(db, assignmentId, teacher.teacherId, sessionClock.now(), confirm)
        return Response(status_code=204)

    @v1.delete(
        "/attempts/{attemptId}", status_code=204,
        responses={401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError}, 409: {"model": ApiError}},
    )
    def deleteStudentAttempt(
        attemptId: str, confirm: Annotated[bool, Query()] = False, teacher: TeacherSession = Depends(currentTeacher),
    ) -> Response:
        with sessionFactory.begin() as db:
            deleteAttempt(db, attemptId, teacher.teacherId, sessionClock.now(), confirm)
        return Response(status_code=204)

    @v1.get(
        "/uploads/{uploadId}",
        response_model_exclude_none=True,
        responses={401: {"model": ApiError}, 404: {"model": ApiError}},
    )
    def readUpload(
        uploadId: str, teacher: TeacherSession = Depends(currentTeacher), event: EventContext = Depends(eventContext),
    ) -> UploadProgress:
        with sessionFactory.begin() as db:
            return getUploadProgress(db, settings, uploadId, teacher.teacherId, event)

    @v1.get("/ocr/recipient", dependencies=[Depends(currentTeacher)])
    def getOcrRecipient(response: Response) -> dict[str, str | None]:
        try:
            hostname = parseOcrUrl(settings.ocrUrl).hostname
        except ValueError:
            hostname = None
        response.headers["Cache-Control"] = "no-store"
        return {"hostname": hostname}

    @v1.get("/uploads/{uploadId}/files/{fileId}/photo", response_class=Response)
    def getUploadPhoto(uploadId: str, fileId: str, teacher: TeacherSession = Depends(currentTeacher)) -> Response:
        with sessionFactory.begin() as db:
            original, media = readUploadPhoto(db, settings, uploadId, fileId, teacher.teacherId)
        return Response(original, media_type=media, headers={
            "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
        })

    @v1.post(
        "/uploads/{uploadId}/bindings",
        responses={
            401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError},
            409: {"model": ApiError}, 422: {"model": ApiError},
        },
    )
    def bindUploadFile(
        uploadId: str, body: BindingRequest, teacher: TeacherSession = Depends(currentTeacher),
        event: EventContext = Depends(eventContext),
    ) -> BindingConfirmed:
        with sessionFactory.begin() as db:
            return confirmBinding(db, uploadId, teacher.teacherId, body, event)

    @v1.get(
        "/checks/{checkId}",
        response_model_exclude_none=True,
        responses={
            401: {"model": ApiError}, 404: {"model": ApiError}, 502: {"model": ApiError},
            503: {"model": ApiError}, 504: {"model": ApiError},
        },
    )
    def readCheck(
        checkId: str, request: Request, response: Response, teacher: TeacherSession = Depends(currentTeacher),
    ) -> CheckStatus:
        with sessionFactory() as db:
            attemptId = requireOwnedCheck(db, checkId, teacher.teacherId)
        response.headers["Cache-Control"] = "no-store"
        return readGradingCheck(settings, checkId, attemptId, getTraceId(request), gradingTransport)

    @v1.get(
        "/assignments/{assignmentId}/report",
        responses={
            401: {"model": ApiError}, 404: {"model": ApiError}, 422: {"model": ApiError},
            502: {"model": ApiError}, 503: {"model": ApiError}, 504: {"model": ApiError},
        },
    )
    def readAssignmentReport(
        assignmentId: str, rev: Annotated[int, Query(ge=1, le=2_147_483_647)], request: Request,
        teacher: TeacherSession = Depends(currentTeacher),
    ) -> AssignmentReport:
        with sessionFactory() as db:
            classId = requireOwnedAssignment(db, assignmentId, rev, teacher.teacherId)
            excluded = deletedReportCheckIds(db, assignmentId, rev)
        params: dict[str, int | list[str]] = {"assignmentRev": rev}
        if excluded:
            params["excludeCheckId"] = excluded
        upstream = sendGradingRequest(
            settings, "GET", f"/internal/v1/assignments/{assignmentId}/report", getTraceId(request), gradingTransport,
            params=params,
        )
        if upstream.status_code == 404 and upstream.headers.get("content-type", "").startswith("application/json") \
                and upstream.json().get("code") == "not_found":
            # grading has not received this revision yet: nothing is counted so far.
            summary = GradingReport(assignmentId=assignmentId, assignmentRev=rev, rev=1, studentCount=0, students=[], tasks=[])
        else:
            summary = readGradingModel(upstream, GradingReport)
        with sessionFactory() as db:
            roster, checks = readReportRoster(db, classId, assignmentId, rev)
            grades = readReportGrades(db, [student.checkId for student in summary.students])
        return combineReport(assignmentId, rev, roster, checks, summary, grades)

    @v1.get(
        "/assignments/{assignmentId}/references",
        responses={
            401: {"model": ApiError}, 404: {"model": ApiError}, 422: {"model": ApiError},
            502: {"model": ApiError}, 503: {"model": ApiError}, 504: {"model": ApiError},
        },
    )
    def readAssignmentReferences(
        assignmentId: str, rev: Annotated[int, Query(ge=1, le=2_147_483_647)], request: Request,
        teacher: TeacherSession = Depends(currentTeacher),
    ) -> AssignmentReferences:
        """Reference answers grading checks this revision against (reference registry of developer 3)."""
        with sessionFactory() as db:
            requireOwnedAssignment(db, assignmentId, rev, teacher.teacherId)
        upstream = sendGradingRequest(
            settings, "GET", f"/internal/v1/assignments/{assignmentId}/references", getTraceId(request),
            gradingTransport, params={"assignmentRev": rev},
        )
        if upstream.status_code == 404 and upstream.headers.get("content-type", "").startswith("application/json") \
                and upstream.json().get("code") == "not_found":
            # The outbox has not delivered this revision to grading yet.
            return AssignmentReferences(assignmentId=assignmentId, assignmentRev=rev, state="queued", tasks=[])
        references = readGradingModel(upstream, AssignmentReferences)
        if (references.assignmentId, references.assignmentRev) != (assignmentId, rev):
            raise invalidGradingResponse()
        return references

    @v1.patch(
        "/checks/{checkId}/review", response_model=ReviewEdited,
        responses={
            401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError},
            409: {"model": ReviewConflict}, 422: {"model": ApiError}, 502: {"model": ApiError},
            503: {"model": ApiError}, 504: {"model": ApiError},
        },
    )
    def editCheckReview(
        checkId: str, body: EditReview, request: Request, teacher: TeacherSession = Depends(currentTeacher),
    ) -> ReviewEdited | Response:
        with sessionFactory() as db:
            attemptId = requireOwnedCheck(db, checkId, teacher.teacherId)
        traceId = getTraceId(request)

        def readBase() -> CheckReady:
            base = readGradingCheck(settings, checkId, attemptId, traceId, gradingTransport, resultRev=body.baseRev)
            if not isinstance(base, CheckReady):
                raise ApiProblem("conflict", "The base revision has no result to edit")
            return base

        upstream = sendGradingRequest(
            settings, "PATCH", f"/internal/v1/checks/{checkId}/review", traceId, gradingTransport,
            body={
                "baseRev": body.baseRev, "editId": body.editId, "actorId": teacher.teacherId,
                "changes": gradingChanges(body.changes, readBase),
            },
        )
        if upstream.status_code == 409:
            return reviewConflictResponse(upstream, traceId)
        receipt = readGradingModel(upstream, ReviewEdited)
        if receipt.checkId != checkId or receipt.editId != body.editId or receipt.resultRev <= body.baseRev:
            raise invalidGradingResponse()
        return receipt

    @v1.put(
        "/checks/{checkId}/grade",
        responses={401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError}, 422: {"model": ApiError}},
    )
    def setCheckGrade(checkId: str, body: SetGrade, teacher: TeacherSession = Depends(currentTeacher)) -> CheckGrade:
        """The teacher grades the submission from 1 to 5; null removes the grade. Repeating the same grade is harmless."""
        with sessionFactory.begin() as db:
            requireOwnedCheck(db, checkId, teacher.teacherId)
            db.execute(update(CheckRecord).where(CheckRecord.checkId == checkId).values(grade=body.grade))
        return CheckGrade(checkId=checkId, grade=body.grade)

    @v1.get(
        "/checks/{checkId}/grade",
        responses={401: {"model": ApiError}, 404: {"model": ApiError}},
    )
    def readCheckGrade(checkId: str, response: Response, teacher: TeacherSession = Depends(currentTeacher)) -> CheckGrade:
        with sessionFactory() as db:
            requireOwnedCheck(db, checkId, teacher.teacherId)
            grade = db.scalar(select(CheckRecord.grade).where(CheckRecord.checkId == checkId))
        response.headers["Cache-Control"] = "no-store"
        return CheckGrade(checkId=checkId, grade=grade)

    @v1.post(
        "/checks/{checkId}/publish", response_model=ReviewRevision,
        responses={
            401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError},
            409: {"model": ReviewConflict}, 422: {"model": ApiError}, 502: {"model": ApiError},
            503: {"model": ApiError}, 504: {"model": ApiError},
        },
    )
    def publishCheckReview(
        checkId: str, body: PublishReview, request: Request, teacher: TeacherSession = Depends(currentTeacher),
    ) -> ReviewRevision | Response:
        with sessionFactory() as db:
            requireOwnedCheck(db, checkId, teacher.teacherId)
        traceId = getTraceId(request)
        upstream = sendGradingRequest(
            settings, "POST", f"/internal/v1/checks/{checkId}/publish", traceId, gradingTransport,
            body={"resultRev": body.resultRev, "actorId": teacher.teacherId},
        )
        if upstream.status_code == 409:
            return reviewConflictResponse(upstream, traceId)
        receipt = readGradingModel(upstream, ReviewRevision)
        if (receipt.checkId, receipt.resultRev) != (checkId, body.resultRev):
            raise invalidGradingResponse()
        return receipt

    @v1.get(
        "/checks/{checkId}/markdown", response_class=Response,
        responses={
            200: {"content": {"text/markdown": {"schema": {"type": "string"}}}},
            401: {"model": ApiError}, 404: {"model": ApiError}, 422: {"model": ApiError},
            502: {"model": ApiError}, 503: {"model": ApiError}, 504: {"model": ApiError},
        },
    )
    def downloadCheckMarkdown(
        checkId: str, rev: Annotated[int, Query(ge=1)], request: Request,
        teacher: TeacherSession = Depends(currentTeacher),
    ) -> Response:
        with sessionFactory() as db:
            requireOwnedCheck(db, checkId, teacher.teacherId)
        upstream = sendGradingRequest(
            settings, "GET", f"/internal/v1/checks/{checkId}/markdown", getTraceId(request), gradingTransport,
            params={"rev": rev},
        )
        return Response(
            content=readMarkdown(upstream), media_type="text/markdown",
            headers={
                "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="feedback-r{rev}.md"',
                "X-Content-Type-Options": "nosniff",
            },
        )

    @v1.get("/sessions/current", responses={401: {"model": ApiError}})
    def readSession(teacher: TeacherSession = Depends(currentTeacher)) -> TeacherSession:
        return teacher

    @v1.delete("/sessions/current", status_code=204, responses={401: {"model": ApiError}, 403: {"model": ApiError}})
    def endSession(request: Request) -> Response:
        with sessionFactory.begin() as db:
            removed = db.scalar(delete(SessionRecord).where(
                SessionRecord.tokenHash == tokenHash(request.cookies.get(COOKIE, "")),
                SessionRecord.expiresAt > sessionClock.now(),
            ).returning(SessionRecord.teacherId))
            if removed is None:
                raise ApiProblem("unauthorized", "Session is invalid or expired")
        response = Response(status_code=204)
        response.delete_cookie(COOKIE, secure=True, httponly=True, samesite="strict")
        return response

    app.include_router(v1)
    return app
