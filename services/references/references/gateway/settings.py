"""Which transport the gateway uses: GATEWAY_TRANSPORT=fixture or gigachat, nothing implicit.

There is no default and no fallback: a missing value, an unknown value or gigachat without a key
stops the gateway at startup. In fixture mode the GigaChat module is not even imported.
"""

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Self

from references.gateway.transports.base import ConfigError, ModelTransport, TransportName
from references.gateway.transports.fixture import DEFAULT_REVIEWS_PATH, FixtureTransport
from references.settings import DEFAULT_DATABASE_URL
from shared.clock import Clock

if TYPE_CHECKING:
    import httpx

TRANSPORTS: dict[str, TransportName] = {"fixture": "fixture", "gigachat": "gigachat"}


DEFAULT_LEASE_S = 90.0
"""How long one provider call may hold its slot. Keep it above the transport timeout (GIGACHAT_TIMEOUT_S, 60 s)."""


@dataclass(frozen=True)
class GatewaySettings:
    transport: TransportName
    reviewFixturesPath: Path = DEFAULT_REVIEWS_PATH
    databaseUrl: str = DEFAULT_DATABASE_URL
    serviceTokensJson: str = "{}"
    leaseS: float = DEFAULT_LEASE_S
    providerLimit: int = 1
    maxAttempts: int = 3
    maxWaitS: float = 30.0

    @property
    def reviewLeaseS(self) -> float:
        """How long one review owns its requestId: every wait plus every attempt."""
        return self.maxWaitS + self.maxAttempts * self.leaseS

    @classmethod
    def fromEnv(cls, environ: Mapping[str, str] = os.environ) -> Self:
        value = environ.get("GATEWAY_TRANSPORT", "").strip()
        transport = TRANSPORTS.get(value)
        if transport is None:
            raise ConfigError(f"GATEWAY_TRANSPORT must be one of {', '.join(TRANSPORTS)}, got {value!r}")
        path = environ.get("GATEWAY_REVIEW_FIXTURES_PATH")
        return cls(
            transport=transport,
            reviewFixturesPath=Path(path) if path else DEFAULT_REVIEWS_PATH,
            databaseUrl=environ.get("GATEWAY_DATABASE_URL", DEFAULT_DATABASE_URL),
            serviceTokensJson=environ.get("GATEWAY_SERVICE_TOKENS", "{}"),
            leaseS=parsePositive(environ, "GATEWAY_LEASE_S", DEFAULT_LEASE_S),
            providerLimit=int(parsePositive(environ, "GATEWAY_PROVIDER_LIMIT", 1, whole=True)),
            maxAttempts=int(parsePositive(environ, "GATEWAY_MAX_ATTEMPTS", 3, whole=True)),
            maxWaitS=parsePositive(environ, "GATEWAY_MAX_WAIT_S", 30.0),
        )


def parsePositive(environ: Mapping[str, str], name: str, default: float, *, whole: bool = False) -> float:
    raw = environ.get(name)
    if raw is None:
        return default
    try:
        value = float(int(raw)) if whole else float(raw)
    except ValueError:
        value = 0
    if value <= 0:
        kind = "a positive whole number" if whole else "a positive number of seconds"
        raise ConfigError(f"{name} must be {kind}, got {raw!r}")
    return value


def buildTransport(
    settings: GatewaySettings,
    environ: Mapping[str, str] = os.environ,
    *,
    clock: Clock | None = None,
    client: "httpx.Client | None" = None,
) -> ModelTransport:
    if settings.transport == "fixture":
        return FixtureTransport.fromFile(settings.reviewFixturesPath)
    from references.gateway.transports.gigachat import GigaChatSettings, GigaChatTransport

    return GigaChatTransport(GigaChatSettings.fromEnv(environ), clock=clock, client=client)
