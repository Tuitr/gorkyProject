import json

import pytest
from pydantic import ValidationError

from contracts.v1.common import ApiError


def test_apiErrorRoundTrip() -> None:
    error = ApiError(code="not_found", message="Object is unavailable", retryable=False, traceId="t1")

    wire = json.loads(error.model_dump_json())

    assert set(wire) == {"code", "message", "retryable", "traceId"}
    assert wire["traceId"] == "t1"
    assert ApiError.model_validate(wire) == error


def test_apiErrorRejectsSnakeCaseTraceId() -> None:
    wire = {"code": "conflict", "message": "Stale revision", "retryable": False, "trace_id": "t1"}

    with pytest.raises(ValidationError):
        ApiError.model_validate(wire)
