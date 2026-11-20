"""HTTP transport to GigaChat Max. The only module that reads provider secrets.

Auth: POST {authUrl} with the authorization key (Basic) and scope gives an access token for about
30 minutes; it is cached until shortly before expires_at. Chat: POST {apiUrl}/chat/completions.
No functions are sent and function_call is "none", so the model cannot call built-in tools.
Retries and the shared concurrency limit live outside this module (C4.3).
"""

import json
import os
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, Self

import httpx

from references.gateway.transports.base import AuthorData, ConfigError, ModelCall, ModelReply, ReviewData, TransportError
from shared.clock import Clock, SystemClock

DEFAULT_AUTH_URL = "https://ngw.devices.sberbank.ru:9443/api/v2/oauth"
DEFAULT_API_URL = "https://gigachat.devices.sberbank.ru/api/v1"
DEFAULT_MODEL = "GigaChat-Max"
DEFAULT_SCOPE = "GIGACHAT_API_PERS"
TOKEN_MARGIN = timedelta(seconds=60)


@dataclass(frozen=True)
class GigaChatSettings:
    authKey: str = field(repr=False)
    scope: str = DEFAULT_SCOPE
    model: str = DEFAULT_MODEL
    authUrl: str = DEFAULT_AUTH_URL
    apiUrl: str = DEFAULT_API_URL
    caBundle: str | None = None
    timeoutS: float = 60.0

    @classmethod
    def fromEnv(cls, environ: Mapping[str, str] = os.environ) -> Self:
        authKey = environ.get("GIGACHAT_AUTH_KEY", "").strip()
        if not authKey:
            raise ConfigError("GATEWAY_TRANSPORT=gigachat needs GIGACHAT_AUTH_KEY; refusing to start")
        return cls(
            authKey=authKey,
            scope=environ.get("GIGACHAT_SCOPE", DEFAULT_SCOPE),
            model=environ.get("GIGACHAT_MODEL", DEFAULT_MODEL),
            authUrl=environ.get("GIGACHAT_AUTH_URL", DEFAULT_AUTH_URL),
            apiUrl=environ.get("GIGACHAT_API_URL", DEFAULT_API_URL).rstrip("/"),
            caBundle=environ.get("GIGACHAT_CA_BUNDLE") or None,
            timeoutS=parseTimeout(environ.get("GIGACHAT_TIMEOUT_S", "60")),
        )


def parseTimeout(value: str) -> float:
    try:
        timeoutS = float(value)
    except ValueError:
        timeoutS = 0
    if timeoutS <= 0:
        raise ConfigError(f"GIGACHAT_TIMEOUT_S must be a positive number of seconds, got {value!r}")
    return timeoutS


def renderData(data: ReviewData | AuthorData) -> str:
    """The user message: the task as JSON data, kept apart from the instructions."""
    return json.dumps(data.model_dump(mode="json"), ensure_ascii=False)


def buildChatBody(call: ModelCall, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": call.instructions},
            {"role": "user", "content": renderData(call.data)},
        ],
        "function_call": "none",
        "stream": False,
    }


def retryAfterSeconds(response: httpx.Response) -> int | None:
    value = response.headers.get("Retry-After", "").strip()
    return int(value) if value.isdigit() else None


def providerMessage(response: httpx.Response) -> str:
    """The provider's own error text ({"message": ...}), cut short. It never holds our key or the request text."""
    try:
        body = response.json()
    except ValueError:
        return ""
    message = body.get("message") if isinstance(body, dict) else None
    return f": {str(message)[:200]}" if message else ""


def statusError(stage: str, response: httpx.Response) -> TransportError:
    status = response.status_code
    message = f"GigaChat {stage} answered HTTP {status}{providerMessage(response)}"
    if status == 429:
        return TransportError("rate_limited", message, retryAfterSeconds(response))
    if status == 504:
        return TransportError("timeout", message)
    if status >= 500:
        return TransportError("unavailable", message, retryAfterSeconds(response))
    return TransportError("internal_error", message)


class GigaChatTransport:
    name: Literal["gigachat"] = "gigachat"

    def __init__(
        self,
        settings: GigaChatSettings,
        clock: Clock | None = None,
        client: httpx.Client | None = None,
        rqUidFactory: Callable[[], str] = lambda: str(uuid.uuid4()),
    ) -> None:
        self.settings = settings
        self.clock = clock if clock is not None else SystemClock()
        verify: str | bool = settings.caBundle if settings.caBundle is not None else True
        self.client = client if client is not None else httpx.Client(verify=verify, timeout=settings.timeoutS)
        self.rqUidFactory = rqUidFactory
        self.accessToken: str | None = None
        self.tokenExpiresAt = datetime.min.replace(tzinfo=UTC)

    def complete(self, call: ModelCall) -> ModelReply:
        response = self.postChat(call, self.token(call))
        if response.status_code == 401:
            self.accessToken = None
            response = self.postChat(call, self.token(call))
        if response.status_code != 200:
            raise statusError("chat", response)
        return self.readReply(response)

    def timeoutFor(self, call: ModelCall) -> float:
        """GIGACHAT_TIMEOUT_S, cut down to what is left of the request budget."""
        if call.deadline is None:
            return self.settings.timeoutS
        leftS = (call.deadline - self.clock.now()).total_seconds()
        if leftS <= 0:
            raise TransportError("timeout", "the request budget ran out before the GigaChat call")
        return min(self.settings.timeoutS, leftS)

    def token(self, call: ModelCall) -> str:
        if self.accessToken is not None and self.clock.now() < self.tokenExpiresAt - TOKEN_MARGIN:
            return self.accessToken
        response = self.send(
            "auth",
            self.timeoutFor(call),
            "POST",
            self.settings.authUrl,
            headers={
                "Authorization": f"Basic {self.settings.authKey}",
                "RqUID": self.rqUidFactory(),
                "Accept": "application/json",
            },
            data={"scope": self.settings.scope},
        )
        if response.status_code != 200:
            raise statusError("auth", response)
        try:
            body = response.json()
            self.accessToken = str(body["access_token"])
            self.tokenExpiresAt = datetime.fromtimestamp(int(body["expires_at"]) / 1000, tz=UTC)
        except (ValueError, KeyError, TypeError) as error:
            raise TransportError("unavailable", "GigaChat auth answered without a usable token") from error
        return self.accessToken

    def postChat(self, call: ModelCall, token: str) -> httpx.Response:
        return self.send(
            "chat",
            self.timeoutFor(call),
            "POST",
            f"{self.settings.apiUrl}/chat/completions",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json", "X-Request-ID": call.requestId},
            json=buildChatBody(call, self.settings.model),
        )

    def send(self, stage: str, timeoutS: float, method: str, url: str, **options: Any) -> httpx.Response:
        try:
            return self.client.request(method, url, timeout=timeoutS, **options)
        except httpx.TimeoutException as error:
            raise TransportError("timeout", f"GigaChat {stage} timed out") from error
        except httpx.TransportError as error:
            raise TransportError("unavailable", f"GigaChat {stage} is unreachable: {type(error).__name__}") from error

    def readReply(self, response: httpx.Response) -> ModelReply:
        try:
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as error:
            message = "GigaChat chat answered without choices[0].message.content"
            raise TransportError("model_invalid_output", message) from error
        if not isinstance(content, str):
            raise TransportError("model_invalid_output", "GigaChat message content is not text")
        providerRequestId = body.get("id") or response.headers.get("x-request-id") or None
        return ModelReply(
            content=content,
            modelId=str(body.get("model") or self.settings.model),
            providerRequestId=str(providerRequestId) if providerRequestId is not None else None,
        )
