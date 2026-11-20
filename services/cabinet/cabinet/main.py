"""FastAPI app of the teacher cabinet: uv run uvicorn cabinet.main:createApp --factory

Public routes live under /v1. The schema is served at /openapi.json.
"""

from fastapi import APIRouter, FastAPI, Request
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cabinet.auth.passwords import Passwords
from cabinet.auth.service import TeacherAccounts
from cabinet.auth.tables import LoginThrottleRecord, TeacherRecord
from cabinet.auth.throttle import LoginThrottle
from cabinet.settings import Settings
from contracts.v1.cabinet.sessions import LoginRequest, TeacherSession
from contracts.v1.common.errors import ApiError
from shared.clock import Clock, SystemClock
from shared.httperrors import ApiProblem, installErrorHandlers

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
    accounts = buildAccounts(
        settings, sessionFactory, passwords if passwords is not None else Passwords(), clock or SystemClock()
    )

    app = FastAPI(title="Teacher cabinet API", version="1.0.0")
    installErrorHandlers(app)
    app.state.teacherAccounts = accounts
    v1 = APIRouter(prefix="/v1")

    @app.get("/ready", include_in_schema=False)
    def readiness() -> dict[str, str]:
        try:
            with sessionFactory() as session:
                session.execute(select(TeacherRecord).limit(0))
                session.execute(select(LoginThrottleRecord).limit(0))
        except SQLAlchemyError as error:
            raise ApiProblem("unavailable", "Cabinet database is unavailable") from error
        return {"status": "ready"}

    @v1.post(
        "/sessions",
        responses={
            401: {"model": ApiError, "description": "Wrong login or password"},
            422: {"model": ApiError, "description": "Invalid request"},
            429: {
                "model": ApiError,
                "description": "Too many login attempts",
                "headers": {"Retry-After": {"schema": {"type": "integer"}, "description": "Delay in seconds"}},
            },
            500: {"model": ApiError, "description": "Internal error"},
        },
    )
    def createSession(body: LoginRequest, request: Request) -> TeacherSession:
        clientAddress = request.client.host if request.client is not None else UNKNOWN_CLIENT
        return accounts.authenticate(body, clientAddress)

    app.include_router(v1)
    return app
