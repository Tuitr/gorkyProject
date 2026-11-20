"""Run the results API with `uv run uvicorn grading.main:createApp --factory`."""

from typing import Annotated

from fastapi import Depends, FastAPI, Query
from sqlalchemy import Engine, create_engine

from contracts.v1.grading.checkresult import CheckStatus, PublishedCheck
from grading.markdownStorage import MarkdownStorage
from grading.results import ArtifactUnavailable, ResultStore
from grading.settings import Settings
from shared.httperrors import ApiProblem, installErrorHandlers
from shared.servicetokens import ServiceIdentity, ServiceTokenRegistry, requireRight


def createApp(settings: Settings | None = None, *, engine: Engine | None = None,
              storage: MarkdownStorage | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.fromEnv()
    store = ResultStore(engine if engine is not None else create_engine(settings.databaseUrl, hide_parameters=True))
    markdown = storage if storage is not None else MarkdownStorage.fromEnv()
    app = FastAPI(title="Grading Results API", version="1.0.0")
    installErrorHandlers(app)
    app.state.serviceTokens = ServiceTokenRegistry.fromJson(settings.serviceTokensJson)

    @app.get("/internal/v1/checks/{checkId}")
    def getCheck(checkId: str, caller: Annotated[ServiceIdentity, Depends(requireRight("checks.read"))],
                 resultRev: Annotated[int | None, Query(ge=1)] = None) -> PublishedCheck | CheckStatus:
        try:
            result = store.read(checkId, resultRev, markdown.require)
        except ArtifactUnavailable as error:
            raise ApiProblem("unavailable", "The published Markdown is currently unavailable") from error
        if result is None:
            raise ApiProblem("not_found", "Check or result revision not found")
        return result

    return app
