"""FastAPI app of the teacher cabinet: uv run uvicorn cabinet.main:createApp --factory

Public routes live under /v1. The schema is served at /openapi.json.
"""

from datetime import timedelta
import hashlib
import secrets

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from sqlalchemy import create_engine, delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cabinet.auth.passwords import Passwords
from cabinet.auth.service import TeacherAccounts
from cabinet.auth.tables import LoginThrottleRecord, SessionRecord, TeacherRecord
from cabinet.auth.throttle import LoginThrottle
from cabinet.settings import Settings
from contracts.v1.cabinet.sessions import LoginRequest, TeacherSession
from contracts.v1.common.errors import ApiError
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

    def currentTeacher(request: Request) -> TeacherSession:
        with sessionFactory() as db:
            teacher = db.scalar(select(TeacherRecord).join(SessionRecord).where(
                SessionRecord.tokenHash == tokenHash(request.cookies.get(COOKIE, "")),
                SessionRecord.expiresAt > sessionClock.now(),
            ))
            if teacher is None:
                raise ApiProblem("unauthorized", "Session is invalid or expired")
            return TeacherSession(teacherId=teacher.teacherId, displayName=teacher.displayName)

    @v1.get("/sessions/current", responses={401: {"model": ApiError}})
    def readSession(response: Response, teacher: TeacherSession = Depends(currentTeacher)) -> TeacherSession:
        response.headers["Cache-Control"] = "no-store"
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
