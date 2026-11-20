"""Run the results API with `uv run uvicorn grading.main:createApp --factory`."""

from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import Engine, create_engine

from contracts.v1.grading.assignmentreport import AssignmentReport
from contracts.v1.grading.checkresult import CheckStatus, PublishedCheck
from contracts.v1.grading.reviewedit import RESULT_REV_HEADER, ReviewEditReceipt, ReviewEditRequest
from grading.markdownStorage import MarkdownStorage
from grading.reports import ReportStore
from grading.results import ArtifactUnavailable, ResultStore
from grading.reviewEdits import InvalidReviewChange, ReviewConflict, ReviewEditor
from grading.settings import Settings
from shared.httperrors import ApiProblem, errorResponse, installErrorHandlers
from shared.servicetokens import ServiceIdentity, ServiceTokenRegistry, requireRight


def createApp(settings: Settings | None = None, *, engine: Engine | None = None,
              storage: MarkdownStorage | None = None) -> FastAPI:
    settings = settings if settings is not None else Settings.fromEnv()
    database = engine if engine is not None else create_engine(settings.databaseUrl, hide_parameters=True)
    store = ResultStore(database)
    reports = ReportStore(database)
    editor = ReviewEditor(database)
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

    @app.get("/internal/v1/assignments/{id}/report")
    def getReport(id: str, caller: Annotated[ServiceIdentity, Depends(requireRight("reports.read"))],
                  rev: Annotated[int | None, Query(ge=1)] = None) -> AssignmentReport:
        report = reports.read(id, rev)
        if report is None:
            raise ApiProblem("not_found", "Assignment or report snapshot not found")
        return report

    @app.patch("/internal/v1/checks/{id}/review", response_model=ReviewEditReceipt)
    def editReview(id: str, edit: ReviewEditRequest, request: Request,
                   caller: Annotated[ServiceIdentity, Depends(requireRight("checks.edit"))]) -> ReviewEditReceipt | JSONResponse:
        try:
            receipt = editor.apply(id, edit)
        except ReviewConflict as error:
            response = errorResponse(request, 409, "conflict", str(error))
            if error.resultRev is not None:
                response.headers[RESULT_REV_HEADER] = str(error.resultRev)
            return response
        except InvalidReviewChange as error:
            raise ApiProblem("invalid_input", str(error)) from error
        if receipt is None:
            raise ApiProblem("not_found", "Check not found")
        return receipt

    return app
