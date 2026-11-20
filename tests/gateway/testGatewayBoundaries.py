"""C7.2: one table of gateway outcomes over real HTTP, the real GigaChat adapter and an intercepted provider.

Every request also carries a signature line with a name. For each case the table fixes the number of chat
calls, the pauses, the final status and code; every case keeps one traceId and leaks the name nowhere:
not in the provider bodies, not in the answer, not in the logs.
"""

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from contracts.v1.common.errors import ApiError
from contracts.v1.references.modelreview import ModelReviewResponse
from references.gateway.main import createApp
from references.gateway.settings import GatewaySettings
from references.gateway.transports.gigachat import GigaChatSettings, GigaChatTransport
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"
TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["model.review"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/internal/v1/model/review"
CHAT_PATH = "/api/v1/chat/completions"
MARKER = "Маркеров"
SIGNATURE = f"Выполнил: {MARKER} Тест Тестович, 9Б"
TASK_RESULT = {
    "answer": "correct",
    "reasoning": "not_shown",
    "method": "unconfirmed",
    "issues": [],
    "needsReview": False,
}

Answer = Callable[[httpx.Request], httpx.Response]


def chat(content: str) -> Answer:
    body = {"id": "gc-1", "model": "GigaChat-Max", "choices": [{"message": {"role": "assistant", "content": content}}]}
    return lambda request: httpx.Response(200, json=body)


def status(code: int, retryAfter: str | None = None) -> Answer:
    headers = {"Retry-After": retryAfter} if retryAfter is not None else {}
    return lambda request: httpx.Response(code, headers=headers)


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("the provider did not answer in time", request=request)


VALID = chat(json.dumps(TASK_RESULT))
NOT_JSON = chat(f"Ученик {MARKER} всё решил верно")


@dataclass
class FakeGigaChat:
    """Answers OAuth with a token and each chat call with the next scripted answer; keeps every chat body."""

    script: list[Answer]
    chatBodies: list[str] = field(default_factory=list)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/oauth"):
            return httpx.Response(200, json={"access_token": "token", "expires_at": 4102444800000})
        assert request.url.path == CHAT_PATH
        self.chatBodies.append(request.content.decode())
        return self.script.pop(0)(request)


@dataclass(frozen=True)
class Case:
    script: list[Answer]
    status: int
    code: str
    chatCalls: int
    sleeps: list[float]
    retryAfter: str | None = None
    freeText: str | None = None
    """A step with personal data in free text; the gateway must stop before any provider call."""


CASES = {
    "success": Case([VALID], 200, "ok", 1, []),
    "rateLimitedThenOk": Case([status(429, "2"), VALID], 200, "ok", 2, [2.0]),
    "rateLimitedExhausted": Case([status(429, "2")] * 3, 429, "rate_limited", 3, [2.0, 2.0], retryAfter="2"),
    "unavailableExhausted": Case([status(503)] * 3, 503, "unavailable", 3, [2.0, 2.0], retryAfter="2"),
    "pauseBeyondBudget": Case([status(429, "120")], 429, "rate_limited", 1, [], retryAfter="120"),
    "timeout": Case([timeout], 504, "timeout", 1, []),
    "invalidJson": Case([NOT_JSON, NOT_JSON], 502, "model_invalid_output", 2, []),
    "personalDataInStep": Case(
        [], 422, "personal_data_suspected", 0, [], freeText=f"Проверил {MARKER} Т. Т., ответ 5"
    ),
}


def requestBody(case: Case, name: str) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    steps = [{"stepId": "s0", "markdown": SIGNATURE}, *body["steps"]]
    if case.freeText is not None:
        steps.append({"stepId": "s2", "markdown": case.freeText})
    return body | {"requestId": f"q-{name}", "steps": steps}


def buildClient(sessionFactory: sessionmaker[Session], fake: FakeGigaChat, clock: ManualClock) -> TestClient:
    transport = GigaChatTransport(
        GigaChatSettings(authKey="key"), clock=clock, client=httpx.Client(transport=httpx.MockTransport(fake))
    )
    settings = GatewaySettings(transport="gigachat", serviceTokensJson=TOKENS)
    return TestClient(createApp(settings, sessionFactory=sessionFactory, transport=transport, clock=clock))


@pytest.fixture
def logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """Every logger at DEBUG, so a leak anywhere (gateway, httpx, FastAPI) would show up."""
    caplog.set_level(logging.DEBUG)
    return caplog


def gatewayEvents(logs: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [json.loads(record.getMessage()) for record in logs.records if record.name == "references.gateway"]


@pytest.mark.parametrize("name", list(CASES))
def testGatewayOutcomeTable(sessionFactory: sessionmaker[Session], logs: pytest.LogCaptureFixture, name: str) -> None:
    case = CASES[name]
    fake = FakeGigaChat(list(case.script))
    clock = ManualClock()
    traceId = f"trace-{name}"

    response = buildClient(sessionFactory, fake, clock).post(
        URL, json=requestBody(case, name), headers=AUTH | {"X-Trace-Id": traceId}
    )

    assert response.status_code == case.status
    assert len(fake.chatBodies) == case.chatCalls
    assert fake.script == []
    assert clock.sleeps == case.sleeps
    assert sum(clock.sleeps) <= GatewaySettings(transport="gigachat").maxWaitS
    assert response.headers.get("Retry-After") == case.retryAfter
    assert response.headers["X-Trace-Id"] == traceId
    if case.status == 200:
        result = ModelReviewResponse.model_validate(response.json())
        assert result.taskResult.taskId == "t1"
        assert result.providerRequestId == "gc-1"
    else:
        error = ApiError.model_validate(response.json())
        assert (error.code, error.traceId) == (case.code, traceId)
    events = gatewayEvents(logs)
    assert events and {event["traceId"] for event in events} == {traceId}
    assert events[-1]["code"] == case.code
    for text in [*fake.chatBodies, response.text, logs.text]:
        assert MARKER not in text
    for body in fake.chatBodies:
        assert [step["stepId"] for step in json.loads(json.loads(body)["messages"][1]["content"])["steps"]] == ["s1"]


def testWorkerRepeatsAfterTimeoutCostOneCallEach(sessionFactory: sessionmaker[Session]) -> None:
    """A slow provider is not retried by the gateway; each repeat of developer 2 is one call, then it stops."""
    fake = FakeGigaChat([timeout, timeout, VALID])
    client = buildClient(sessionFactory, fake, ManualClock())
    body = requestBody(CASES["timeout"], "timeoutRepeated")

    statuses = [client.post(URL, json=body, headers=AUTH).status_code for _ in range(4)]

    assert statuses == [504, 504, 200, 200]
    assert len(fake.chatBodies) == 3
