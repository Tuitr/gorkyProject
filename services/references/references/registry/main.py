"""FastAPI app of the reference registry: uv run uvicorn references.registry.main:createApp --factory"""

from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.references.reference import ReferenceRequest, ReferenceResult, referenceResultAdapter
from references.providers.base import ReferenceProvider
from references.providers.fixture import FixtureProvider
from references.registry.service import ReferenceService
from references.registry.store import ReferenceStore
from references.settings import Settings
from shared.clock import Clock
from shared.httperrors import ApiProblem, installErrorHandlers
from shared.servicetokens import ServiceIdentity, ServiceTokenRegistry, requireRight

REQUEST_RIGHT = "references.request"


def resultResponse(result: ReferenceResult, status: int) -> JSONResponse:
    return JSONResponse(status_code=status, content=referenceResultAdapter.dump_python(result, mode="json"))


def createApp(
    settings: Settings | None = None,
    *,
    sessionFactory: sessionmaker[Session] | None = None,
    provider: ReferenceProvider | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    settings = settings if settings is not None else Settings.fromEnv()
    if sessionFactory is None:
        sessionFactory = sessionmaker(create_engine(settings.databaseUrl), expire_on_commit=False)
    if provider is None:
        provider = (
            FixtureProvider.fromFile()
            if settings.fixturesPath is None
            else FixtureProvider.fromFile(Path(settings.fixturesPath))
        )
    service = ReferenceService(ReferenceStore(sessionFactory), provider, clock)

    app = FastAPI(title="Reference API", version="1.0.0")
    installErrorHandlers(app)
    app.state.serviceTokens = ServiceTokenRegistry.fromJson(settings.serviceTokensJson)
    app.state.referenceService = service

    @app.post("/internal/v1/references")
    def requestReference(
        body: ReferenceRequest, caller: ServiceIdentity = Depends(requireRight(REQUEST_RIGHT))
    ) -> JSONResponse:
        result = service.request(body)
        return resultResponse(result, 202 if result.state == "pending" else 200)

    @app.get("/internal/v1/references/{referenceId}")
    def getReference(
        referenceId: str, caller: ServiceIdentity = Depends(requireRight(REQUEST_RIGHT))
    ) -> JSONResponse:
        result = service.get(referenceId)
        if result is None:
            raise ApiProblem("not_found", "No reference with this id")
        return resultResponse(result, 200)

    return app
