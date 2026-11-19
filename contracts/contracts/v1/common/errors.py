"""HTTP error envelope shared by every service: {code, message, retryable, traceId}."""

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
