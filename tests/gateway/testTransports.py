"""C4.1: fixture and GigaChat Max transports, chosen only by the environment."""

import json
import socket
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from contracts.v1 import openapi
from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest
from references.gateway.settings import GatewaySettings, buildTransport
from references.gateway.transports.base import (
    ConfigError,
    ModelCall,
    ProviderReference,
    ReviewData,
    TransportError,
)
from references.gateway.transports.fixture import FixtureTransport
from references.gateway.transports.gigachat import GigaChatSettings, GigaChatTransport
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"
AUTH_KEY = "c2VjcmV0LWF1dGgta2V5"
TOKEN = "access-token-1"
INSTRUCTIONS = "Проверь решение по критериям. Верни JSON taskResult."
Handler = Callable[[httpx.Request], httpx.Response]


def buildCall(**dataChanges: Any) -> ModelCall:
    request = ModelReviewRequest.model_validate_json((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    data = ReviewData(
        statement=request.statement,
        rubric=tuple(request.rubric),
        steps=tuple(request.steps),
        reference=ProviderReference(schemaId=request.reference.schemaId, value=request.reference.value),
    )
    return ModelCall(
        requestId=request.requestId,
        promptVersion=request.promptVersion,
        taskId=request.taskId,
        instructions=INSTRUCTIONS,
        data=data.model_copy(update=dataChanges),
    )


def tokenResponse(clock: ManualClock, lifetimeS: int = 1800) -> httpx.Response:
    expiresAt = int((clock.now().timestamp() + lifetimeS) * 1000)
    return httpx.Response(200, json={"access_token": TOKEN, "expires_at": expiresAt})


def chatResponse(content: str = '{"answer": "correct"}', **headers: str) -> httpx.Response:
    body = {
        "choices": [{"message": {"role": "assistant", "content": content}, "index": 0, "finish_reason": "stop"}],
        "model": "GigaChat-Max:2.0",
        "object": "chat.completion",
    }
    return httpx.Response(200, json=body, headers=headers)


class FakeGigaChat:
    """Records every request; answers auth with a token and chat with the next queued response."""

    def __init__(self, clock: ManualClock, chat: list[httpx.Response | Exception] | None = None) -> None:
        self.clock = clock
        self.chat = list(chat) if chat is not None else []
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path == "/api/v2/oauth":
            return tokenResponse(self.clock)
        answer = self.chat.pop(0) if self.chat else chatResponse()
        if isinstance(answer, Exception):
            raise answer
        return answer

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


def buildLive(fake: FakeGigaChat) -> GigaChatTransport:
    return GigaChatTransport(
        GigaChatSettings(authKey=AUTH_KEY),
        clock=fake.clock,
        client=httpx.Client(transport=httpx.MockTransport(fake)),
        rqUidFactory=lambda: "6f0b1291-c7f3-43c6-bb2e-9f3efb2dc98e",
    )


def testLiveAdapterBuildsTheProviderRequest() -> None:
    fake = FakeGigaChat(ManualClock(), [chatResponse('{"answer": "correct"}', **{"x-request-id": "p-42"})])
    call = buildCall()

    reply = buildLive(fake).complete(call)

    auth, chat = fake.requests
    assert str(auth.url) == "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
    assert auth.headers["Authorization"] == f"Basic {AUTH_KEY}"
    assert auth.headers["RqUID"] == "6f0b1291-c7f3-43c6-bb2e-9f3efb2dc98e"
    assert auth.content == b"scope=GIGACHAT_API_PERS"
    assert str(chat.url) == "https://gigachat.devices.sberbank.ru/api/v1/chat/completions"
    assert chat.headers["Authorization"] == f"Bearer {TOKEN}"
    assert chat.headers["X-Request-ID"] == call.requestId
    body = json.loads(chat.content)
    assert body == {
        "model": "GigaChat-Max",
        "messages": [
            {"role": "system", "content": INSTRUCTIONS},
            {"role": "user", "content": body["messages"][1]["content"]},
        ],
        "function_call": "none",
        "stream": False,
    }
    assert json.loads(body["messages"][1]["content"]) == {
        "statement": "Длина вектора (3; 4)",
        "rubric": ["Нужно показать вычисление по формуле длины вектора"],
        "steps": [{"stepId": "s1", "markdown": "Ответ: 5"}],
        "reference": {"schemaId": "scalar.v1", "value": {"exact": "5"}},
    }
    assert AUTH_KEY not in chat.content.decode()
    assert reply.content == '{"answer": "correct"}'
    assert reply.modelId == "GigaChat-Max:2.0"
    assert reply.providerRequestId == "p-42"


def testInternalIdsStayOutOfTheProviderBody() -> None:
    fake = FakeGigaChat(ManualClock())
    call = buildCall()

    buildLive(fake).complete(call)

    sent = fake.requests[1].content.decode()
    for internalValue in ('"r1"', '"t1"', '"review.v1"', '"q1"', "referenceId", "taskId", "promptVersion"):
        assert internalValue not in sent


def testTokenIsReusedUntilShortlyBeforeExpiry() -> None:
    clock = ManualClock()
    fake = FakeGigaChat(clock)
    transport = buildLive(fake)

    transport.complete(buildCall())
    clock.advance(1700)
    transport.complete(buildCall())
    clock.advance(60)
    transport.complete(buildCall())

    assert fake.paths() == [
        "/api/v2/oauth",
        "/api/v1/chat/completions",
        "/api/v1/chat/completions",
        "/api/v2/oauth",
        "/api/v1/chat/completions",
    ]


def testExpiredTokenIsRefreshedOnce() -> None:
    fake = FakeGigaChat(ManualClock(), [httpx.Response(401), chatResponse()])

    reply = buildLive(fake).complete(buildCall())

    assert reply.content == '{"answer": "correct"}'
    assert fake.paths() == ["/api/v2/oauth", "/api/v1/chat/completions", "/api/v2/oauth", "/api/v1/chat/completions"]


def testRepeatedUnauthorizedStops() -> None:
    fake = FakeGigaChat(ManualClock(), [httpx.Response(401), httpx.Response(401)])

    with pytest.raises(TransportError) as raised:
        buildLive(fake).complete(buildCall())

    assert (raised.value.code, raised.value.retryable) == ("internal_error", False)
    assert len(fake.requests) == 4


@pytest.mark.parametrize(
    ("answer", "code", "retryAfterS"),
    [
        (httpx.Response(429, headers={"Retry-After": "7"}), "rate_limited", 7),
        (httpx.Response(429), "rate_limited", None),
        (httpx.Response(503, headers={"Retry-After": "3"}), "unavailable", 3),
        (httpx.Response(500), "unavailable", None),
        (httpx.Response(504), "timeout", None),
        (httpx.ReadTimeout("slow"), "timeout", None),
        (httpx.ConnectError("refused"), "unavailable", None),
        (httpx.Response(400, json={"message": "bad"}), "internal_error", None),
        (httpx.Response(200, json={"choices": []}), "model_invalid_output", None),
        (httpx.Response(200, text="not json"), "model_invalid_output", None),
    ],
    ids=["429", "429NoHeader", "503", "500", "504", "readTimeout", "connectError", "400", "noChoices", "notJson"],
)
def testProviderFailuresMapToContractCodes(
    answer: httpx.Response | Exception, code: str, retryAfterS: int | None
) -> None:
    fake = FakeGigaChat(ManualClock(), [answer])

    with pytest.raises(TransportError) as raised:
        buildLive(fake).complete(buildCall())

    assert raised.value.code == code
    assert raised.value.retryAfterS == retryAfterS
    assert fake.paths().count("/api/v1/chat/completions") == 1
    assert AUTH_KEY not in raised.value.message
    assert "Длина вектора" not in raised.value.message


def testSettingsHideTheKey() -> None:
    settings = GigaChatSettings.fromEnv({"GIGACHAT_AUTH_KEY": AUTH_KEY})

    assert AUTH_KEY not in repr(settings)
    assert (settings.model, settings.scope) == ("GigaChat-Max", "GIGACHAT_API_PERS")


def testFixtureTransportReturnsTheRecordedReply() -> None:
    reply = FixtureTransport.fromFile().complete(buildCall())

    expected = json.loads((EXAMPLES / "responseAnswerOnly.json").read_text(encoding="utf-8"))
    assert TaskResult.model_validate_json(reply.content) == TaskResult.model_validate(expected["taskResult"])
    assert (reply.modelId, reply.providerRequestId) == ("fixture", None)


@pytest.fixture
def networkAttempts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Counts every attempt to open a socket or send an httpx request; each attempt fails."""
    attempts: list[str] = []

    def refuseConnect(self: socket.socket, address: object) -> None:
        attempts.append(f"socket {address!r}")
        raise OSError("network is off in this test")

    def refuseRequest(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
        attempts.append(f"httpx {request.url}")
        raise httpx.ConnectError("network is off in this test")

    monkeypatch.setattr(socket.socket, "connect", refuseConnect)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", refuseRequest)
    return attempts


def testMissingFixtureIsLocalErrorWithoutExternalCalls(networkAttempts: list[str]) -> None:
    environ = {"GATEWAY_TRANSPORT": "fixture", "GIGACHAT_AUTH_KEY": AUTH_KEY}
    transport = buildTransport(GatewaySettings.fromEnv(environ), environ)
    unknown = buildCall(statement="Длина вектора (6; 8)")

    with pytest.raises(TransportError) as raised:
        transport.complete(unknown)

    assert transport.name == "fixture"
    assert (raised.value.code, raised.value.retryable) == ("internal_error", False)
    assert "no external call" in raised.value.message
    assert networkAttempts == []


def testOtherStudentStepsDoNotMatchTheFixture() -> None:
    call = buildCall()
    otherSteps = call.data.steps[0].model_copy(update={"markdown": "Ответ: 7"})

    with pytest.raises(TransportError):
        FixtureTransport.fromFile().complete(buildCall(steps=(otherSteps,)))


@pytest.mark.parametrize(
    "environ",
    [{}, {"GATEWAY_TRANSPORT": ""}, {"GATEWAY_TRANSPORT": "openai"}, {"GATEWAY_TRANSPORT": "GigaChat"}],
    ids=["missing", "empty", "unknown", "wrongCase"],
)
def testTransportMustBeChosenExplicitly(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigError, match="GATEWAY_TRANSPORT"):
        GatewaySettings.fromEnv(environ)


@pytest.mark.parametrize("authKey", [None, "", "   "], ids=["missing", "empty", "blank"])
def testLiveTransportWithoutKeyDoesNotStart(authKey: str | None) -> None:
    environ = {"GATEWAY_TRANSPORT": "gigachat"}
    if authKey is not None:
        environ["GIGACHAT_AUTH_KEY"] = authKey

    with pytest.raises(ConfigError, match="GIGACHAT_AUTH_KEY"):
        buildTransport(GatewaySettings.fromEnv(environ), environ)


def testLiveTransportIsBuiltWithoutCallingTheProvider(networkAttempts: list[str]) -> None:
    environ = {"GATEWAY_TRANSPORT": "gigachat", "GIGACHAT_AUTH_KEY": AUTH_KEY, "GIGACHAT_TIMEOUT_S": "15"}

    transport = buildTransport(GatewaySettings.fromEnv(environ), environ)

    assert isinstance(transport, GigaChatTransport)
    assert transport.settings.timeoutS == 15
    assert networkAttempts == []


@pytest.mark.parametrize("value", ["0", "-1", "soon"])
def testBadTimeoutDoesNotStart(value: str) -> None:
    with pytest.raises(ConfigError, match="GIGACHAT_TIMEOUT_S"):
        GigaChatSettings.fromEnv({"GIGACHAT_AUTH_KEY": AUTH_KEY, "GIGACHAT_TIMEOUT_S": value})


def testPackagedReviewFixturesLoad() -> None:
    transport = FixtureTransport.fromFile()

    assert transport.fixtures
    for fixture in transport.fixtures:
        if isinstance(fixture.content, dict):
            TaskResult.model_validate(fixture.content)
