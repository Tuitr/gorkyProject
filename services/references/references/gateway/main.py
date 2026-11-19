"""FastAPI app of the model gateway: uv run uvicorn references.gateway.main:createApp --factory

A separate process and container from the registry; it shares only the PostgreSQL schema `refs`.
"""

from fastapi import Depends, FastAPI
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.common.errors import ERROR_STATUS
from contracts.v1.references.modelreview import PERSONAL_DATA_SUSPECTED, ModelReviewRequest
from references.gateway.idempotency import ReviewJournal
from references.gateway.prompts import PromptCatalog
from references.gateway.service import ReviewService
from references.gateway.settings import GatewaySettings, buildTransport
from references.gateway.transports.base import GatewayError, ModelTransport
from shared.clock import Clock, SystemClock
from shared.httperrors import ApiProblem, installErrorHandlers
from shared.servicetokens import ServiceIdentity, ServiceTokenRegistry, requireRight

REVIEW_RIGHT = "model.review"
GATEWAY_STATUS = ERROR_STATUS | {PERSONAL_DATA_SUSPECTED: 422}


def toProblem(error: GatewayError) -> ApiProblem:
    return ApiProblem(
        error.code, error.message, status=GATEWAY_STATUS.get(error.code, 500), retryAfterS=error.retryAfterS
    )


def createApp(
    settings: GatewaySettings | None = None,
    *,
    sessionFactory: sessionmaker[Session] | None = None,
    transport: ModelTransport | None = None,
    clock: Clock | None = None,
    prompts: PromptCatalog | None = None,
) -> FastAPI:
    settings = settings if settings is not None else GatewaySettings.fromEnv()
    clock = clock if clock is not None else SystemClock()
    if sessionFactory is None:
        sessionFactory = sessionmaker(create_engine(settings.databaseUrl), expire_on_commit=False)
    if transport is None:
        transport = buildTransport(settings, clock=clock)
    journal = ReviewJournal(sessionFactory, clock, settings.leaseS)
    service = ReviewService(journal, transport, prompts if prompts is not None else PromptCatalog())

    app = FastAPI(title="Model gateway", version="1.0.0")
    installErrorHandlers(app)
    app.state.serviceTokens = ServiceTokenRegistry.fromJson(settings.serviceTokensJson)
    app.state.reviewService = service

    @app.post("/internal/v1/model/review")
    def reviewTask(
        body: ModelReviewRequest, caller: ServiceIdentity = Depends(requireRight(REVIEW_RIGHT))
    ) -> JSONResponse:
        try:
            response = service.review(body)
        except GatewayError as error:
            raise toProblem(error) from error
        return JSONResponse(status_code=200, content=response.model_dump(mode="json"))

    return app
