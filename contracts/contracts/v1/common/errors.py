"""HTTP error envelope shared by every service: {code, message, retryable, traceId}."""

from typing import Final

from pydantic import BaseModel, ConfigDict


class ApiError(BaseModel):
    """Error body for any non-2xx HTTP answer.

    401/403 access, 404 object unavailable, 409 revision or idempotency key conflict,
    422 schema error. Retry 429, 503 and timeouts with the same requestId.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: str
    message: str
    retryable: bool
    traceId: str


RETRYABLE_CODES: Final = frozenset({"rate_limited", "unavailable", "timeout"})
"""Codes a caller may retry with the same requestId, honouring Retry-After."""

ERROR_STATUS: Final[dict[str, int]] = {
    "unauthorized": 401,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "invalid_input": 422,
    "rate_limited": 429,
    "internal_error": 500,
    "model_invalid_output": 502,
    "unavailable": 503,
    "timeout": 504,
}
"""Known error codes of v1 and their HTTP status. Services may add codes; meaning of these is fixed."""
