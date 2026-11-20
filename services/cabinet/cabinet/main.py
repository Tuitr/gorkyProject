"""FastAPI app of the teacher cabinet: uv run uvicorn cabinet.main:createApp --factory

Public routes live under /v1. The schema is served at /openapi.json.
"""

from datetime import date, timedelta
import hashlib
import secrets
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, FastAPI, File, Form, Request, Response, UploadFile
from pydantic import Json
from starlette.datastructures import UploadFile as MultipartFile
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cabinet.assignments import AssignmentRecord, requireOwnedAssignment, storeAssignment
from cabinet.auth.passwords import Passwords
from cabinet.auth.service import TeacherAccounts
from cabinet.auth.tables import LoginThrottleRecord, SessionRecord, TeacherRecord
from cabinet.auth.throttle import LoginThrottle
from cabinet.bindingconfirmation import confirmBinding
from cabinet.classes import ClassRecord, StudentRecord, requireOwnedClass
from cabinet.documents import readDocx
from cabinet.settings import Settings
from cabinet.uploads import UploadFileRecord, UploadRecord, getUploadProgress, readSubmission, storeUpload
from contracts.v1.cabinet.assignments import AssignmentVersion
from contracts.v1.cabinet.bindings import BindingConfirmed, BindingRequest
from contracts.v1.cabinet.classes import ClassSummary, CreateClass, CreateStudent, StudentSummary
from contracts.v1.cabinet.sessions import LoginRequest, TeacherSession
from contracts.v1.cabinet.uploads import UploadAccepted, UploadProgress
from contracts.v1.common.errors import ApiError
from contracts.v1.events.checks import TeacherRequirement
from shared.clock import Clock, SystemClock
from shared.httperrors import ApiProblem, installErrorHandlers

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
) -> FastAPI:
    settings = settings if settings is not None else Settings.fromEnv()
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
        except SQLAlchemyError as error:
            raise ApiProblem("unavailable", "Cabinet database is unavailable") from error
        return {"status": "ready"}

    @v1.post(
        "/sessions",
        responses={
            401: {"model": ApiError, "description": "Wrong login or password"},
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
                select(ClassRecord).where(ClassRecord.teacherId == teacher.teacherId)
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

    @v1.get("/classes/{classId}/students", responses={401: {"model": ApiError}, 404: {"model": ApiError}})
    def listStudents(classId: str, teacher: TeacherSession = Depends(currentTeacher)) -> list[StudentSummary]:
        with sessionFactory() as db:
            requireOwnedClass(db, classId, teacher.teacherId)
            rows = db.scalars(
                select(StudentRecord).where(StudentRecord.classId == classId)
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
            requireOwnedClass(db, classId, teacher.teacherId)
            db.add(StudentRecord(studentId=created.studentId, classId=classId, name=created.name))
        return created

    @v1.post(
        "/classes/{classId}/assignments",
        status_code=201,
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
        teacher: TeacherSession = Depends(currentTeacher),
    ) -> AssignmentVersion:
        with sessionFactory.begin() as db:
            requireOwnedClass(db, classId, teacher.teacherId)
            return storeAssignment(
                db, settings, classId, assignmentId, assignmentDate or sessionClock.now().date(),
                requirements, readDocx(file),
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
    ) -> UploadAccepted:
        with sessionFactory.begin() as db:
            classId = requireOwnedAssignment(db, assignmentId, assignmentRev, teacher.teacherId)
            return storeUpload(
                db, settings, assignmentId, assignmentRev, classId, readSubmission(file), sessionClock.now(),
            )

    @v1.get(
        "/uploads/{uploadId}",
        response_model_exclude_none=True,
        responses={401: {"model": ApiError}, 404: {"model": ApiError}},
    )
    def readUpload(uploadId: str, teacher: TeacherSession = Depends(currentTeacher)) -> UploadProgress:
        with sessionFactory.begin() as db:
            return getUploadProgress(db, settings, uploadId, teacher.teacherId, sessionClock.now())

    @v1.post(
        "/uploads/{uploadId}/bindings",
        responses={
            401: {"model": ApiError}, 403: {"model": ApiError}, 404: {"model": ApiError},
            409: {"model": ApiError}, 422: {"model": ApiError},
        },
    )
    def bindUploadFile(
        uploadId: str, body: BindingRequest, teacher: TeacherSession = Depends(currentTeacher),
    ) -> BindingConfirmed:
        with sessionFactory.begin() as db:
            return confirmBinding(db, uploadId, teacher.teacherId, body, sessionClock.now())

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
