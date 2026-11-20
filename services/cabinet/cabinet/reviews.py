"""Translate teacher review edits for grading and pass its receipts and Markdown back to the teacher."""

from collections.abc import Callable

import httpx
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from cabinet.gradingclient import invalidGradingResponse
from contracts.v1.cabinet.reviews import RemoveIssue, ReviewChange, ReviewConflict, UpdateIssue, UpdateVerdict
from contracts.v1.common.errors import ApiError
from contracts.v1.grading.checks import CheckReady
from contracts.v1.grading.reviewedit import RESULT_REV_HEADER
from shared.httperrors import ApiProblem

MARKDOWN_LIMIT = 2 * 1024 * 1024


def reviewConflictResponse(response: httpx.Response, traceId: str) -> JSONResponse:
    """grading reports the latest revision in X-Result-Rev; older drafts put it into the error body."""
    try:
        error = ApiError.model_validate_json(response.content).model_dump(mode="json")
        header = response.headers.get(RESULT_REV_HEADER)
        conflict = ReviewConflict.model_validate({**response.json(), **error, **({"resultRev": int(header)} if header else {})})
    except (ValidationError, ValueError):
        raise invalidGradingResponse() from None
    body = ReviewConflict(
        code="conflict", message="Review revision conflict", retryable=False,
        traceId=traceId, resultRev=conflict.resultRev,
    )
    return JSONResponse(status_code=409, content=body.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


def issueTask(issueId: str, readBase: Callable[[], CheckReady]) -> str:
    owners = [task.taskId for task in readBase().taskResults if any(issue.issueId == issueId for issue in task.issues)]
    if len(owners) != 1:
        raise ApiProblem("invalid_input", "Name the task of this issue: taskId is missing or the issue is not unique")
    return owners[0]


def gradingChanges(changes: list[ReviewChange], readBase: Callable[[], CheckReady]) -> list[dict[str, object]]:
    """The public typed changes as grading review operations; readBase is called once, only when a taskId is missing."""
    cached: list[CheckReady] = []

    def base() -> CheckReady:
        if not cached:
            cached.append(readBase())
        return cached[0]

    translated: list[dict[str, object]] = []
    for change in changes:
        match change:
            case RemoveIssue():
                taskId = change.taskId or issueTask(change.issueId, base)
                translated.append({"op": "removeIssue", "taskId": taskId, "issueId": change.issueId})
            case UpdateIssue():
                taskId = change.taskId or issueTask(change.issueId, base)
                translated.append({
                    "op": "updateIssue", "taskId": taskId, "issueId": change.issueId,
                    "comment": change.comment, "correction": change.correction,
                })
            case UpdateVerdict():
                verdict = change.model_dump(mode="json", exclude={"type"}, exclude_none=True)
                translated.append({"op": "setVerdict", **verdict})
            case _:
                translated.append({"op": "setOverallReview", "overallReview": change.overallReview})
    return translated


def readMarkdown(response: httpx.Response) -> bytes:
    if response.status_code == 404:
        raise ApiProblem("not_found", "Published review not found")
    if response.status_code != 200:
        raise ApiProblem("unavailable", "Grading service is unavailable")
    media = response.headers.get("content-type", "").split(";")[0].strip()
    if media != "text/markdown" or len(response.content) > MARKDOWN_LIMIT:
        raise invalidGradingResponse()
    try:
        response.content.decode("utf-8")
    except UnicodeDecodeError:
        raise invalidGradingResponse() from None
    return response.content
