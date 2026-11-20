"""Run the results API with `uv run uvicorn grading.main:createApp --factory`."""

from typing import Annotated

from fastapi import Depends, FastAPI, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine, create_engine

from contracts.v1.grading.assignmentreferences import AssignmentReferences
from contracts.v1.grading.assignmentreport import AssignmentReport
from contracts.v1.grading.checkresult import CheckStatus, PublishedCheck
from contracts.v1.grading.reviewedit import RESULT_REV_HEADER, ReviewEditReceipt, ReviewEditRequest
from contracts.v1.grading.reviewpublication import PublishReviewReceipt, PublishReviewRequest
from grading.markdownStorage import MarkdownStorage
from grading.referenceView import ReferenceReader
from grading.reports import ReportStore
from grading.resultArtifacts import publishResultArtifact
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
    referenceReader = ReferenceReader(database)
    editor = ReviewEditor(database)
    markdown = storage if storage is not None else MarkdownStorage.fromEnv()
    app = FastAPI(title="Grading Results API", version="1.0.0")
    installErrorHandlers(app)
    app.state.serviceTokens = ServiceTokenRegistry.fromJson(settings.serviceTokensJson)

    @app.get("/internal/v1/checks/{checkId}", response_model_exclude_unset=True)
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
                  rev: Annotated[int | None, Query(ge=1)] = None,
                  assignmentRev: Annotated[int | None, Query(ge=1)] = None,
                  excludeCheckId: Annotated[list[str] | None, Query()] = None) -> AssignmentReport:
        if rev is not None and (assignmentRev is not None or excludeCheckId):
            raise ApiProblem("invalid_input", "rev reads a saved snapshot; assignmentRev and excludeCheckId build a new one")
        report = reports.read(id, rev, assignmentRev=assignmentRev, excludedCheckIds=frozenset(excludeCheckId or ()))
        if report is None:
            raise ApiProblem("not_found", "Assignment or report snapshot not found")
        return report

    @app.get("/internal/v1/assignments/{id}/references")
    def getReferences(id: str, caller: Annotated[ServiceIdentity, Depends(requireRight("checks.read"))],
                      assignmentRev: Annotated[int | None, Query(ge=1)] = None) -> AssignmentReferences:
        references = referenceReader.read(id, assignmentRev)
        if references is None:
            raise ApiProblem("not_found", "Assignment revision not found")
        return references

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
        try:
            publishResultArtifact(store, id, receipt.resultRev, markdown)
        except ArtifactUnavailable as error:
            raise ApiProblem("unavailable", "The edit is saved and its Markdown is awaiting publication") from error
        return receipt

    @app.post("/internal/v1/checks/{id}/publish")
    def publishReview(id: str, publication: PublishReviewRequest,
                      caller: Annotated[ServiceIdentity, Depends(requireRight("checks.publish"))]) -> PublishReviewReceipt:
        try:
            result = store.read(id, publication.resultRev, markdown.require)
        except ArtifactUnavailable as error:
            raise ApiProblem("unavailable", "The requested Markdown is currently unavailable") from error
        if result is None:
            raise ApiProblem("not_found", "Check or result revision not found")
        if not isinstance(result, PublishedCheck):
            raise ApiProblem("conflict", "The requested result revision is still awaiting Markdown")
        store.recordAcceptance(id, publication.resultRev, actorId=publication.actorId)
        return PublishReviewReceipt(checkId=id, resultRev=publication.resultRev, markdownRef=result.markdownRef)

    @app.get("/internal/v1/checks/{id}/markdown", response_class=Response)
    def downloadMarkdown(id: str, rev: Annotated[int, Query(ge=1)],
                         caller: Annotated[ServiceIdentity, Depends(requireRight("checks.read"))]) -> Response:
        ref = store.acceptedReference(id, rev)
        if ref is None:
            raise ApiProblem("not_found", "Published result revision not found")
        try:
            body = markdown.download(ref)
        except ArtifactUnavailable as error:
            raise ApiProblem("unavailable", "The published Markdown is currently unavailable") from error
        return Response(body, media_type="text/markdown", headers={"Content-Disposition": f'attachment; filename="review-{rev}.md"'})

    return app
