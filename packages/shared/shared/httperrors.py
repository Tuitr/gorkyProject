"""Every error leaves a service as ApiError {code, message, retryable, traceId}.

Messages are fixed texts: never the request body, student work or secrets.
"""

import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from contracts.v1.common.errors import ERROR_STATUS, RETRYABLE_CODES, ApiError

TRACE_HEADER = "X-Trace-Id"
TRACE_ID = re.compile(r"[A-Za-z0-9._-]{1,64}")

STATUS_CODES: dict[int, str] = {status: code for code, status in ERROR_STATUS.items()}


class ApiProblem(Exception):
    """Raise inside a route to answer with a contract error."""

    def __init__(self, code: str, message: str, *, status: int | None = None, retryAfterS: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status if status is not None else ERROR_STATUS.get(code, 500)
        self.retryAfterS = retryAfterS


def getTraceId(request: Request) -> str:
    """One traceId per request: the caller's X-Trace-Id if it looks like an id, otherwise a new one."""
    known = getattr(request.state, "traceId", None)
    if isinstance(known, str):
        return known
    incoming = request.headers.get(TRACE_HEADER, "")
    traceId = incoming if TRACE_ID.fullmatch(incoming) else uuid.uuid4().hex
    request.state.traceId = traceId
    return traceId


async def assignTraceId(request: Request, callNext: Callable[[Request], Awaitable[Response]]) -> Response:
    traceId = getTraceId(request)
    response = await callNext(request)
    response.headers.setdefault(TRACE_HEADER, traceId)
    return response


def errorResponse(
    request: Request, status: int, code: str, message: str, retryAfterS: int | None = None
) -> JSONResponse:
    traceId = getTraceId(request)
    body = ApiError(code=code, message=message, retryable=code in RETRYABLE_CODES, traceId=traceId)
    headers = {TRACE_HEADER: traceId}
    if retryAfterS is not None:
        headers["Retry-After"] = str(retryAfterS)
    return JSONResponse(status_code=status, content=body.model_dump(mode="json"), headers=headers)


async def handleApiProblem(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, ApiProblem)
    return errorResponse(request, exc.status, exc.code, exc.message, exc.retryAfterS)


async def handleValidationError(request: Request, exc: Exception) -> JSONResponse:
    return errorResponse(request, 422, "invalid_input", "Request does not match the schema")


async def handleHttpException(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    code = STATUS_CODES.get(exc.status_code, "internal_error")
    return errorResponse(request, exc.status_code, code, "Request failed")


async def handleUnexpected(request: Request, exc: Exception) -> JSONResponse:
    return errorResponse(request, 500, "internal_error", "Internal error")


def installErrorHandlers(app: FastAPI) -> None:
    """ApiError for every failure, and the same X-Trace-Id on the response, in errors and in logs."""
    app.middleware("http")(assignTraceId)
    app.add_exception_handler(ApiProblem, handleApiProblem)
    app.add_exception_handler(RequestValidationError, handleValidationError)
    app.add_exception_handler(StarletteHTTPException, handleHttpException)
    app.add_exception_handler(Exception, handleUnexpected)
