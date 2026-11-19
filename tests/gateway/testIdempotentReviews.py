"""C3.3: a repeated review call with the same requestId asks the model at most once per stored answer."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from contracts.v1.common.errors import ApiError
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from references.gateway.idempotency import Claim, ReviewJournal
from references.gateway.main import createApp
from references.gateway.prompts import PromptCatalog
from references.gateway.settings import GatewaySettings
from references.gateway.tables import ReviewCallRecord
from references.gateway.transports.base import GatewayError, ModelCall, ModelReply, TransportError
from references.gateway.transports.fixture import FixtureTransport
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"
TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["model.review"]}, "other": {"serviceName": "x"}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/internal/v1/model/review"
WAIT_S = 10


class ScriptedTransport:
    """Counts calls; each call takes the next scripted outcome, then falls back to the fixture reply."""

    name: Literal["fixture"] = "fixture"

    def __init__(self, *outcomes: Exception | threading.Event) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[ModelCall] = []
        self.started = threading.Event()
        self.fixtures = FixtureTransport.fromFile()

    def complete(self, call: ModelCall) -> ModelReply:
        self.calls.append(call)
        self.started.set()
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        if isinstance(outcome, threading.Event):
            assert outcome.wait(WAIT_S)
        return self.fixtures.complete(call)


def requestBody(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    return body | changes


def buildClient(
    sessionFactory: sessionmaker[Session], transport: ScriptedTransport, clock: ManualClock | None = None
) -> TestClient:
    settings = GatewaySettings(transport="fixture", serviceTokensJson=TOKENS, leaseS=90)
    return TestClient(createApp(settings, sessionFactory=sessionFactory, transport=transport, clock=clock))


def readRecords(sessionFactory: sessionmaker[Session]) -> list[ReviewCallRecord]:
    with sessionFactory() as session:
        return list(session.scalars(select(ReviewCallRecord)))


def countRecords(sessionFactory: sessionmaker[Session]) -> int:
    with sessionFactory() as session:
        return session.scalar(select(func.count()).select_from(ReviewCallRecord)) or 0


def testFirstCallReturnsTheModelAnswer(sessionFactory: sessionmaker[Session]) -> None:
    transport = ScriptedTransport()

    response = buildClient(sessionFactory, transport).post(URL, json=requestBody(), headers=AUTH)

    assert response.status_code == 200
    expected = json.loads((EXAMPLES / "responseAnswerOnly.json").read_text(encoding="utf-8"))
    assert response.json() == expected
    ModelReviewResponse.model_validate(response.json())
    (record,) = readRecords(sessionFactory)
    assert (record.state, record.attempts, record.leaseToken) == ("succeeded", 1, None)


def testRepeatAfterSuccessDoesNotCallTheModel(sessionFactory: sessionmaker[Session]) -> None:
    transport = ScriptedTransport()
    client = buildClient(sessionFactory, transport)
    body = requestBody()

    first = client.post(URL, json=body, headers=AUTH)
    reordered = dict(reversed(list(body.items())))
    second = client.post(URL, json=reordered, headers=AUTH)

    assert second.status_code == 200
    assert second.json() == first.json()
    assert len(transport.calls) == 1
    assert countRecords(sessionFactory) == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"steps": [{"stepId": "s1", "markdown": "Ответ: 7"}]},
        {"promptVersion": "review.v2"},
        {"rubric": []},
        {"reference": {"referenceId": "r2", "schemaId": "scalar.v1", "value": {"exact": "5"}}},
    ],
    ids=["steps", "promptVersion", "rubric", "referenceId"],
)
def testSameRequestIdWithAnotherBodyIsConflict(sessionFactory: sessionmaker[Session], changes: dict[str, Any]) -> None:
    transport = ScriptedTransport()
    client = buildClient(sessionFactory, transport)
    client.post(URL, json=requestBody(), headers=AUTH)

    response = client.post(URL, json=requestBody(**changes), headers=AUTH)

    assert response.status_code == 409
    error = ApiError.model_validate(response.json())
    assert error.retryable is False
    assert error.traceId
    assert len(transport.calls) == 1
    (record,) = readRecords(sessionFactory)
    assert record.state == "succeeded"


def testParallelRepeatWaitsAndModelIsCalledOnce(sessionFactory: sessionmaker[Session]) -> None:
    release = threading.Event()
    transport = ScriptedTransport(release)
    client = buildClient(sessionFactory, transport)

    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, URL, json=requestBody(), headers=AUTH)
        assert transport.started.wait(WAIT_S)
        duplicate = client.post(URL, json=requestBody(), headers=AUTH)
        conflicting = client.post(URL, json=requestBody(steps=[{"stepId": "s1", "markdown": "7"}]), headers=AUTH)
        release.set()
        firstResponse = first.result(timeout=WAIT_S)
    repeat = client.post(URL, json=requestBody(), headers=AUTH)

    assert duplicate.status_code == 503
    assert duplicate.json()["code"] == "unavailable"
    assert duplicate.json()["retryable"] is True
    assert duplicate.headers["Retry-After"] == "300"
    assert conflicting.status_code == 409
    assert firstResponse.status_code == 200
    assert repeat.json() == firstResponse.json()
    assert len(transport.calls) == 1


def testFailedCallIsRetriedWithTheSameRequestId(sessionFactory: sessionmaker[Session]) -> None:
    transport = ScriptedTransport(TransportError("timeout", "GigaChat chat timed out"))
    client = buildClient(sessionFactory, transport)

    failed = client.post(URL, json=requestBody(), headers=AUTH)
    (afterFailure,) = readRecords(sessionFactory)
    retried = client.post(URL, json=requestBody(), headers=AUTH)

    assert failed.status_code == 504
    assert failed.json()["code"] == "timeout"
    assert failed.json()["retryable"] is True
    assert (afterFailure.state, afterFailure.leaseToken, afterFailure.lastErrorCode) == ("pending", None, "timeout")
    assert retried.status_code == 200
    assert [call.requestId for call in transport.calls] == ["q1", "q1"]
    (record,) = readRecords(sessionFactory)
    assert (record.state, record.attempts) == ("succeeded", 2)


def testInvalidModelAnswerIsNotStored(sessionFactory: sessionmaker[Session]) -> None:
    class BrokenTransport(ScriptedTransport):
        def complete(self, call: ModelCall) -> ModelReply:
            self.calls.append(call)
            return ModelReply(content='{"answer": "maybe"}', modelId="fixture")

    transport = BrokenTransport()
    client = buildClient(sessionFactory, transport)

    first = client.post(URL, json=requestBody(), headers=AUTH)
    second = client.post(URL, json=requestBody(), headers=AUTH)

    assert first.status_code == 502
    assert first.json()["code"] == "model_invalid_output"
    assert second.status_code == 502
    assert len(transport.calls) == 2
    (record,) = readRecords(sessionFactory)
    assert (record.state, record.response, record.lastErrorCode) == ("pending", None, "model_invalid_output")


def testStoredAnswerDoesNotNeedThePromptAgain(sessionFactory: sessionmaker[Session], tmp_path: Path) -> None:
    transport = ScriptedTransport()
    client = buildClient(sessionFactory, transport)
    first = client.post(URL, json=requestBody(), headers=AUTH)
    emptyPrompts = PromptCatalog(tmp_path)
    settings = GatewaySettings(transport="fixture", serviceTokensJson=TOKENS)
    app = createApp(settings, sessionFactory=sessionFactory, transport=transport, prompts=emptyPrompts)

    repeat = TestClient(app).post(URL, json=requestBody(), headers=AUTH)

    assert repeat.json() == first.json()
    assert len(transport.calls) == 1


def testRefusedRequestLeavesNoRecord(sessionFactory: sessionmaker[Session]) -> None:
    transport = ScriptedTransport()
    body = requestBody(steps=[{"stepId": "s1", "markdown": "Маркеров Тест Тестович: ответ 5"}])

    response = buildClient(sessionFactory, transport).post(URL, json=body, headers=AUTH)

    assert response.status_code == 422
    assert response.json()["code"] == "personal_data_suspected"
    assert "Маркеров" not in response.text
    assert transport.calls == []
    assert countRecords(sessionFactory) == 0


@pytest.mark.parametrize(("headers", "status"), [({}, 401), ({"Authorization": "Bearer other"}, 403)])
def testCallerNeedsTheReviewRight(sessionFactory: sessionmaker[Session], headers: dict[str, str], status: int) -> None:
    transport = ScriptedTransport()

    response = buildClient(sessionFactory, transport).post(URL, json=requestBody(), headers=headers)

    assert response.status_code == status
    assert transport.calls == []


def buildJournal(sessionFactory: sessionmaker[Session], clock: ManualClock) -> ReviewJournal:
    return ReviewJournal(sessionFactory, clock, leaseS=90)


def answer(modelId: str) -> ModelReviewResponse:
    body = json.loads((EXAMPLES / "responseAnswerOnly.json").read_text(encoding="utf-8"))
    return ModelReviewResponse.model_validate(body | {"modelId": modelId})


def testExpiredLeaseIsTakenOverAndFirstAnswerWins(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    journal = buildJournal(sessionFactory, clock)
    request = ModelReviewRequest.model_validate(requestBody())

    crashed = journal.begin(request)
    clock.advance(91)
    takeover = journal.begin(request)
    assert isinstance(crashed, Claim) and isinstance(takeover, Claim)
    journal.fail(crashed, "timeout")
    stillOwned = journal.read(request.requestId)
    stored = journal.succeed(takeover, answer("second"))
    late = journal.succeed(crashed, answer("first"))

    assert crashed.leaseToken != takeover.leaseToken
    assert stillOwned is not None and stillOwned.leaseToken == takeover.leaseToken
    assert stored.modelId == "second"
    assert late.modelId == "second"
    record = journal.read(request.requestId)
    assert record is not None
    assert (record.state, record.attempts, record.response) == ("succeeded", 2, answer("second").model_dump(mode="json"))


def testLeaseInForceMakesARepeatWait(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    journal = buildJournal(sessionFactory, clock)
    request = ModelReviewRequest.model_validate(requestBody())

    journal.begin(request)
    clock.advance(60.5)
    with pytest.raises(GatewayError) as raised:
        journal.begin(request)

    assert (raised.value.code, raised.value.retryAfterS, raised.value.retryable) == ("unavailable", 30, True)
