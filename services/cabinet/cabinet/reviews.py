"""Grading receipts and immutable Markdown handed out by the cabinet."""

import hashlib
from contextlib import closing

import httpx
from botocore.exceptions import BotoCoreError, ClientError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from cabinet.documents import buildS3Client
from cabinet.gradingclient import invalidGradingResponse
from cabinet.settings import Settings
from contracts.v1.cabinet.reviews import MarkdownRef, ReviewConflict
from shared.httperrors import ApiProblem


def reviewConflictResponse(response: httpx.Response, traceId: str) -> JSONResponse:
    try:
        conflict = ReviewConflict.model_validate_json(response.content)
    except ValidationError:
        raise invalidGradingResponse() from None
    body = ReviewConflict(
        code="conflict", message="Review revision conflict", retryable=False,
        traceId=traceId, resultRev=conflict.resultRev,
    )
    return JSONResponse(status_code=409, content=body.model_dump(mode="json"), headers={"Cache-Control": "no-store"})


def loadMarkdown(settings: Settings, ref: MarkdownRef) -> bytes:
    try:
        with closing(buildS3Client(settings)) as client, closing(client.get_object(Bucket=ref.bucket, Key=ref.key)["Body"]) as stored:
            content = stored.read()
    except (ClientError, BotoCoreError):
        raise ApiProblem("unavailable", "Review storage is unavailable") from None
    if hashlib.sha256(content).hexdigest() != ref.sha256:
        raise invalidGradingResponse()
    return content
