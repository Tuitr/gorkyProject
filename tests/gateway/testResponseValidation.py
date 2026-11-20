"""C4.4: the model answer must be a valid taskResult; otherwise 502 after the format budget, with clean logs."""

import json
import logging
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from contracts.v1.common.errors import ApiError
from contracts.v1.references.modelreview import ModelReviewResponse
from references.gateway.diagnostics import SAFE_KEYS, logEvent
from references.gateway.main import createApp
from references.gateway.settings import GatewaySettings
from references.gateway.tables import ReviewCallRecord
from references.gateway.transports.base import ModelCall, ModelReply, TransportError

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"
TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["model.review"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/internal/v1/model/review"
SECRET_ANSWER = "СЕКРЕТНЫЙ_ОТВЕТ_МОДЕЛИ"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    return loaded


def taskResultText(**changes: Any) -> str:
    taskResult = example("responseAnswerOnly.json")["taskResult"]
    del taskResult["taskId"]
    return json.dumps(taskResult | changes, ensure_ascii=False)


class Replies:
    """Returns the given contents (or raises the given errors) one call after another."""

    name: Literal["fixture"] = "fixture"

    def __init__(self, *outcomes: str | Exception, providerRequestId: str | None = None) -> None:
        self.outcomes = list(outcomes)
        self.providerRequestId = providerRequestId
        self.calls: list[ModelCall] = []

    def complete(self, call: ModelCall) -> ModelReply:
        self.calls.append(call)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return ModelReply(content=outcome, modelId="GigaChat-Max", providerRequestId=self.providerRequestId)


def buildClient(sessionFactory: sessionmaker[Session], transport: Replies) -> TestClient:
    settings = GatewaySettings(transport="fixture", serviceTokensJson=TOKENS)
    return TestClient(createApp(settings, sessionFactory=sessionFactory, transport=transport))


def gatewayLogs(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(record.getMessage()) for record in caplog.records if record.name == "references.gateway"]


def readRecord(sessionFactory: sessionmaker[Session]) -> ReviewCallRecord:
    with sessionFactory() as session:
        return session.scalars(select(ReviewCallRecord)).one()


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="references.gateway")
    return caplog


@pytest.mark.parametrize(
    ("content", "problem"),
    [
        ('{"answer": "correct", "reasoning": ' + f'"{SECRET_ANSWER}', "json: not valid JSON"),
        (f'["{SECRET_ANSWER}"]', "json: expected an object, got list"),
        (taskResultText(answer=SECRET_ANSWER), "answer: literal_error"),
        (taskResultText(method="maybe"), "method: literal_error"),
        (taskResultText(issues=[{"issueId": "i1", "stepId": "s1"}]), "issues.0.category: missing"),
        (taskResultText(**{SECRET_ANSWER: 1}), "?: extra_forbidden"),
        ('{"answer": "correct"}', "reasoning: missing"),
    ],
    ids=["brokenJson", "notObject", "unknownAnswer", "unknownMethod", "issueMissingField", "extraField", "missing"],
)
def testInvalidAnswerIs502AfterTheFormatBudget(
    sessionFactory: sessionmaker[Session], logs: pytest.LogCaptureFixture, content: str, problem: str
) -> None:
    transport = Replies(content, content)

    response = buildClient(sessionFactory, transport).post(URL, json=example("request.json"), headers=AUTH)

    assert response.status_code == 502
    error = ApiError.model_validate(response.json())
    assert (error.code, error.retryable) == ("model_invalid_output", False)
    assert error.traceId == response.headers["X-Trace-Id"]
    assert "taskResult" not in response.json()
    assert len(transport.calls) == 2
    record = readRecord(sessionFactory)
    assert (record.state, record.response, record.lastErrorCode) == ("pending", None, "model_invalid_output")
    events = gatewayLogs(logs)
    invalid = [event for event in events if event["event"] == "review.invalidOutput"]
    assert [event["attempt"] for event in invalid] == [1, 2]
    assert all(problem in event["problems"] for event in invalid)
    assert events[-1] | {"durationMs": 0} == {
        "event": "review.failed",
        "stage": "transport",
        "code": "model_invalid_output",
        "message": "the model did not return a valid taskResult.v1 in 2 attempts",
        "attempt": 2,
        "requestId": "q1",
        "traceId": error.traceId,
        "durationMs": 0,
    }
    for leaked in (SECRET_ANSWER, "Длина вектора", "Ответ: 5"):
        assert leaked not in logs.text


def testSecondAttemptCanFixTheFormat(sessionFactory: sessionmaker[Session], logs: pytest.LogCaptureFixture) -> None:
    transport = Replies("not json", taskResultText())

    response = buildClient(sessionFactory, transport).post(URL, json=example("request.json"), headers=AUTH)

    assert response.status_code == 200
    assert response.json()["taskResult"]["answer"] == "correct"
    assert len(transport.calls) == 2
    assert [event["event"] for event in gatewayLogs(logs)] == ["review.invalidOutput", "review.succeeded"]
    assert readRecord(sessionFactory).state == "succeeded"


def testBrokenProviderEnvelopeCountsAsAFormatAttempt(sessionFactory: sessionmaker[Session]) -> None:
    envelope = TransportError("model_invalid_output", "GigaChat chat answered without choices[0].message.content")
    transport = Replies(envelope, taskResultText())

    response = buildClient(sessionFactory, transport).post(URL, json=example("request.json"), headers=AUTH)

    assert response.status_code == 200
    assert len(transport.calls) == 2


def testValidAnswerKeepsProviderRequestId(sessionFactory: sessionmaker[Session], logs: pytest.LogCaptureFixture) -> None:
    expected = example("responseWithIssues.json")
    modelAnswer = dict(expected["taskResult"])
    del modelAnswer["taskId"]
    transport = Replies(json.dumps(modelAnswer, ensure_ascii=False), providerRequestId="p-123")
    body = example("request.json") | {"taskId": "t2"}

    response = buildClient(sessionFactory, transport).post(URL, json=body, headers=AUTH)

    assert response.status_code == 200
    assert ModelReviewResponse.model_validate(response.json()) == ModelReviewResponse.model_validate(expected)
    assert response.json()["providerRequestId"] == "p-123"
    (succeeded,) = [event for event in gatewayLogs(logs) if event["event"] == "review.succeeded"]
    assert succeeded["providerRequestId"] == "p-123"
    assert succeeded["modelId"] == "GigaChat-Max"
    assert isinstance(succeeded["durationMs"], int)


def testStepIdsAreLeftToTheWorker(sessionFactory: sessionmaker[Session]) -> None:
    """The issues point to s2..s4 while the request has only s1: the gateway checks shape, not meaning."""
    modelAnswer = dict(example("responseWithIssues.json")["taskResult"])
    del modelAnswer["taskId"]
    transport = Replies(json.dumps(modelAnswer, ensure_ascii=False))

    response = buildClient(sessionFactory, transport).post(URL, json=example("request.json"), headers=AUTH)

    assert response.status_code == 200
    assert [issue["stepId"] for issue in response.json()["taskResult"]["issues"]] == ["s2", "s3", "s4"]


@pytest.mark.parametrize(
    "content",
    [f"```json\n{taskResultText()}\n```", f"```\n{taskResultText()}\n```\n", taskResultText(taskId="t999")],
    ids=["jsonFence", "plainFence", "modelTaskId"],
)
def testTolerableAnswerForms(sessionFactory: sessionmaker[Session], content: str) -> None:
    transport = Replies(content)

    response = buildClient(sessionFactory, transport).post(URL, json=example("request.json"), headers=AUTH)

    assert response.status_code == 200
    assert response.json()["taskResult"]["taskId"] == "t1"
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    ("incoming", "kept"), [("trace-abc.1", True), ("bad id with spaces", False), ("x" * 65, False)]
)
def testOneTraceIdInBodyHeaderAndLogs(
    sessionFactory: sessionmaker[Session], logs: pytest.LogCaptureFixture, incoming: str, kept: bool
) -> None:
    transport = Replies("not json", "not json")

    response = buildClient(sessionFactory, transport).post(
        URL, json=example("request.json"), headers=AUTH | {"X-Trace-Id": incoming}
    )

    traceId = response.json()["traceId"]
    assert (traceId == incoming) is kept
    assert response.headers["X-Trace-Id"] == traceId
    assert {event["traceId"] for event in gatewayLogs(logs)} == {traceId}


def testSuccessCarriesTheTraceHeader(sessionFactory: sessionmaker[Session]) -> None:
    response = buildClient(sessionFactory, Replies(taskResultText())).post(
        URL, json=example("request.json"), headers=AUTH | {"X-Trace-Id": "trace-ok"}
    )

    assert response.status_code == 200
    assert response.headers["X-Trace-Id"] == "trace-ok"


def testRefusalIsLoggedWithoutTheText(sessionFactory: sessionmaker[Session], logs: pytest.LogCaptureFixture) -> None:
    body = example("request.json") | {"steps": [{"stepId": "s1", "markdown": "Маркеров Тест Тестович: ответ 5"}]}

    response = buildClient(sessionFactory, Replies()).post(URL, json=body, headers=AUTH)

    assert response.status_code == 422
    (event,) = gatewayLogs(logs)
    assert (event["stage"], event["code"], event["requestId"]) == ("sanitize", "personal_data_suspected", "q1")
    assert "Маркеров" not in logs.text


def testEveryLogLineIsJsonWithSafeKeysOnly(sessionFactory: sessionmaker[Session], logs: pytest.LogCaptureFixture) -> None:
    client = buildClient(sessionFactory, Replies("not json", taskResultText()))
    client.post(URL, json=example("request.json"), headers=AUTH)
    client.post(URL, json=example("request.json"), headers=AUTH)

    events = gatewayLogs(logs)
    assert [event["event"] for event in events] == ["review.invalidOutput", "review.succeeded", "review.cached"]
    for event in events:
        assert set(event) <= SAFE_KEYS
        assert {"requestId", "traceId", "stage", "code", "durationMs"} <= set(event)


def testLogEventRefusesUnknownKeys() -> None:
    with pytest.raises(ValueError, match="statement"):
        logEvent("review.failed", statement="Длина вектора (3; 4)")
