"""The cabinet validates the grading response and reports service failures without provider details."""

from collections.abc import Callable

import httpx
import pytest

from cabinet.checks import readGradingCheck
from cabinet.settings import Settings
from contracts.v1.common.errors import ApiError
from shared.httperrors import ApiProblem

SETTINGS = Settings(gradingServiceToken="test-cabinet")


def testKnownCheckCanBeQueuedBeforeTheWorkerReadsIt(contractExample: Callable[[str], dict[str, object]]) -> None:
    def missing(request: httpx.Request) -> httpx.Response:
        error = ApiError(code="not_found", message="No check with this id", retryable=False, traceId="test")
        return httpx.Response(404, json=error.model_dump(mode="json"))

    result = readGradingCheck(SETTINGS, "check-1", "attempt-1", "test", httpx.MockTransport(missing))

    assert result.model_dump(mode="json") == contractExample("grading/checkQueued.json")


@pytest.mark.parametrize(
    ("response", "code", "status"),
    [
        (httpx.Response(503, text="provider detail"), "unavailable", 503),
        (httpx.Response(404, json={"detail": "Not Found"}), "grading_invalid_response", 502),
        (httpx.Response(200, text="malformed"), "grading_invalid_response", 502),
        (httpx.Response(200, json={"state": "ready", "checkId": "check-1", "attemptId": "attempt-1"}),
         "grading_invalid_response", 502),
    ],
    ids=["unavailable", "unknown-route", "invalid-json", "incomplete-result"],
)
def testGradingFailureHasAFixedPublicError(response: httpx.Response, code: str, status: int) -> None:
    with pytest.raises(ApiProblem) as raised:
        readGradingCheck(SETTINGS, "check-1", "attempt-1", "test", httpx.MockTransport(lambda request: response))

    assert (raised.value.code, raised.value.status) == (code, status)
    assert "provider detail" not in raised.value.message


@pytest.mark.parametrize("field", ["checkId", "attemptId"])
def testCheckAndAttemptMustMatchTheCabinet(field: str, contractExample: Callable[[str], dict[str, object]]) -> None:
    reply = {**contractExample("grading/checkReady.json"), field: "wrong-id"}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=reply))

    with pytest.raises(ApiProblem) as raised:
        readGradingCheck(SETTINGS, "check-1", "attempt-1", "test", transport)

    assert (raised.value.code, raised.value.status) == ("grading_invalid_response", 502)


@pytest.mark.parametrize(
    ("failure", "code", "status"),
    [(httpx.ReadTimeout, "timeout", 504), (httpx.ConnectError, "unavailable", 503)],
)
def testGradingTransportFailure(
    failure: type[httpx.RequestError], code: str, status: int,
) -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise failure("internal network details", request=request)

    with pytest.raises(ApiProblem) as raised:
        readGradingCheck(SETTINGS, "check-1", "attempt-1", "test", httpx.MockTransport(fail))

    assert (raised.value.code, raised.value.status) == (code, status)
    assert "internal network details" not in raised.value.message
