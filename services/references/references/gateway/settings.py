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
"""How long one worker owns a review call. Keep it above the transport timeout (GIGACHAT_TIMEOUT_S, 60 s)."""


@dataclass(frozen=True)
class GatewaySettings:
    transport: TransportName
    reviewFixturesPath: Path = DEFAULT_REVIEWS_PATH
    databaseUrl: str = DEFAULT_DATABASE_URL
    serviceTokensJson: str = "{}"
    leaseS: float = DEFAULT_LEASE_S

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
            leaseS=parseLease(environ.get("GATEWAY_LEASE_S", str(DEFAULT_LEASE_S))),
        )


def parseLease(value: str) -> float:
    try:
        leaseS = float(value)
    except ValueError:
        leaseS = 0
    if leaseS <= 0:
        raise ConfigError(f"GATEWAY_LEASE_S must be a positive number of seconds, got {value!r}")
    return leaseS


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
