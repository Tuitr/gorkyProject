"""The gateway answers within GATEWAY_REQUEST_BUDGET_S, before the 60 s client timeout of developer 2."""

import json
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from contracts.v1.references.modelreview import ModelReviewRequest
from references.gateway.limiter import LimitedTransport, ProviderLimiter, RetryPolicy
from references.gateway.main import createApp
from references.gateway.prompts import PromptCatalog
from references.gateway.sanitize import buildModelCall
from references.gateway.settings import DEFAULT_REQUEST_BUDGET_S, GatewaySettings
from references.gateway.tables import ReviewCallRecord
from references.gateway.transports.base import ModelCall, ModelReply, TransportError
from references.gateway.transports.fixture import FixtureTransport
from references.gateway.transports.gigachat import GigaChatSettings, GigaChatTransport
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"
TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["model.review"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/internal/v1/model/review"
GRADING_CLIENT_TIMEOUT_S = 60


def requestBody() -> dict[str, Any]:
    body: dict[str, Any] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    return body


def buildCall(deadlineInS: float | None) -> tuple[ManualClock, ModelCall]:
    clock = ManualClock()
    call = buildModelCall(ModelReviewRequest.model_validate(requestBody()), PromptCatalog())
    if deadlineInS is not None:
        call = call.model_copy(update={"deadline": clock.now() + timedelta(seconds=deadlineInS)})
    return clock, call


class SlowModel:
    """Each call takes takesS of the manual clock; the first answers are not taskResult, the rest are fixtures."""

    name: Literal["fixture"] = "fixture"

    def __init__(self, clock: ManualClock, takesS: float, badAnswers: int) -> None:
        self.clock = clock
        self.takesS = takesS
        self.badAnswers = badAnswers
        self.calls: list[ModelCall] = []
        self.fixtures = FixtureTransport.fromFile()

    def complete(self, call: ModelCall) -> ModelReply:
        self.calls.append(call)
        self.clock.advance(self.takesS)
        if len(self.calls) <= self.badAnswers:
            return ModelReply(content="not json", modelId="GigaChat-Max")
        return self.fixtures.complete(call)


def buildClient(sessionFactory: sessionmaker[Session], model: SlowModel, budgetS: float) -> TestClient:
    settings = GatewaySettings(transport="fixture", serviceTokensJson=TOKENS, requestBudgetS=budgetS)
    return TestClient(createApp(settings, sessionFactory=sessionFactory, transport=model, clock=model.clock))


def testDefaultBudgetEndsBeforeTheGradingClientGivesUp() -> None:
    settings = GatewaySettings.fromEnv({"GATEWAY_TRANSPORT": "fixture"})

    assert settings.requestBudgetS == DEFAULT_REQUEST_BUDGET_S
    assert settings.requestBudgetS < GRADING_CLIENT_TIMEOUT_S
    assert settings.requestBudgetS < settings.reviewLeaseS


def testNoNewAttemptStartsAfterTheBudget(sessionFactory: sessionmaker[Session]) -> None:
    model = SlowModel(ManualClock(), takesS=45, badAnswers=1)
    client = buildClient(sessionFactory, model, budgetS=40)

    late = client.post(URL, json=requestBody(), headers=AUTH)

    assert late.status_code == 503
    assert (late.json()["code"], late.json()["retryable"]) == ("unavailable", True)
    assert late.headers["Retry-After"] == "1"
    assert len(model.calls) == 1
    with sessionFactory() as session:
        record = session.scalars(select(ReviewCallRecord)).one()
    assert (record.state, record.leaseToken, record.lastErrorCode) == ("pending", None, "unavailable")

    repeat = client.post(URL, json=requestBody(), headers=AUTH)

    assert repeat.status_code == 200
    assert len(model.calls) == 2


def testEveryAttemptSeesTheSameDeadline(sessionFactory: sessionmaker[Session]) -> None:
    model = SlowModel(ManualClock(), takesS=10, badAnswers=1)
    started = model.clock.now()

    response = buildClient(sessionFactory, model, budgetS=40).post(URL, json=requestBody(), headers=AUTH)

    assert response.status_code == 200
    assert [call.deadline for call in model.calls] == [started + timedelta(seconds=40)] * 2


class Paused:
    name: Literal["fixture"] = "fixture"

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, call: ModelCall) -> ModelReply:
        self.calls += 1
        raise TransportError("rate_limited", "HTTP 429", 5)


def testRetryPauseDoesNotRunPastTheDeadline(sessionFactory: sessionmaker[Session]) -> None:
    clock, call = buildCall(deadlineInS=3)
    inner = Paused()
    limiter = ProviderLimiter(sessionFactory, clock, "fixture", 1, leaseS=90)
    policy = RetryPolicy(maxAttempts=3, maxWaitS=30, defaultBackoffS=2, pollS=0.5)

    with pytest.raises(TransportError) as raised:
        LimitedTransport(inner, limiter, clock, policy).complete(call)

    assert (raised.value.code, raised.value.retryAfterS) == ("rate_limited", 5)
    assert inner.calls == 1
    assert clock.sleeps == []


def tokenResponse(clock: ManualClock) -> httpx.Response:
    return httpx.Response(200, json={"access_token": "t", "expires_at": int(clock.now().timestamp() + 1800) * 1000})


def timeoutsOf(requests: list[httpx.Request]) -> list[float]:
    return [request.extensions["timeout"]["read"] for request in requests]


def testGigaChatTimeoutIsCutToWhatIsLeft() -> None:
    clock, call = buildCall(deadlineInS=20)
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path == "/api/v2/oauth":
            clock.advance(5)
            return tokenResponse(clock)
        content = (EXAMPLES / "responseAnswerOnly.json").read_text(encoding="utf-8")
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}], "model": "GigaChat-Max"})

    client = httpx.Client(transport=httpx.MockTransport(answer))
    GigaChatTransport(GigaChatSettings(authKey="key", timeoutS=60), clock=clock, client=client).complete(call)

    assert timeoutsOf(requests) == [20, 15]


def testGigaChatIsNotCalledWithoutTimeLeft() -> None:
    clock, call = buildCall(deadlineInS=1)
    clock.advance(2)
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(answer))
    with pytest.raises(TransportError) as raised:
        GigaChatTransport(GigaChatSettings(authKey="key"), clock=clock, client=client).complete(call)

    assert raised.value.code == "timeout"
    assert requests == []


def testWithoutDeadlineTheConfiguredTimeoutStays() -> None:
    clock, call = buildCall(deadlineInS=None)
    requests: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return tokenResponse(clock)

    client = httpx.Client(transport=httpx.MockTransport(answer))
    transport = GigaChatTransport(GigaChatSettings(authKey="key", timeoutS=42), clock=clock, client=client)
    transport.token(call)

    assert timeoutsOf(requests) == [42]
