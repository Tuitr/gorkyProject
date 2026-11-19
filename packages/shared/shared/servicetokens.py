"""Service-to-service authorization for /internal/v1 routes.

Each calling service has its own bearer token and a set of operation rights.
A teacher session cookie never authorizes an internal call.
"""

import hmac
import json
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from fastapi import Request

from shared.httperrors import ApiProblem


@dataclass(frozen=True)
class ServiceIdentity:
    serviceName: str
    rights: frozenset[str]


class ServiceTokenRegistry:
    def __init__(self, tokens: Mapping[str, ServiceIdentity]) -> None:
        self.tokens = dict(tokens)

    @classmethod
    def fromJson(cls, raw: str) -> "ServiceTokenRegistry":
        """Parse {"<token>": {"serviceName": "grading", "rights": ["references.request"]}}."""
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise ValueError("service tokens must be a JSON object")
        tokens: dict[str, ServiceIdentity] = {}
        for token, entry in parsed.items():
            if not isinstance(entry, dict) or not isinstance(entry.get("serviceName"), str):
                raise ValueError("each token needs serviceName and rights")
            rights = entry.get("rights", [])
            if not isinstance(rights, list) or not all(isinstance(right, str) for right in rights):
                raise ValueError("rights must be a list of strings")
            tokens[str(token)] = ServiceIdentity(entry["serviceName"], frozenset(rights))
        return cls(tokens)

    def identify(self, token: str) -> ServiceIdentity | None:
        for knownToken, identity in self.tokens.items():
            if hmac.compare_digest(knownToken.encode(), token.encode()):
                return identity
        return None


def readBearerToken(request: Request) -> str | None:
    header = request.headers.get("Authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None
    return token.strip()


def requireRight(right: str) -> Callable[[Request], Awaitable[ServiceIdentity]]:
    """FastAPI dependency: the caller must present a known service token holding `right`.

    The registry is read from app.state.serviceTokens.
    """

    async def checkServiceToken(request: Request) -> ServiceIdentity:
        registry: ServiceTokenRegistry = request.app.state.serviceTokens
        token = readBearerToken(request)
        identity = registry.identify(token) if token else None
        if identity is None:
            raise ApiProblem("unauthorized", "Service token is missing or invalid")
        if right not in identity.rights:
            raise ApiProblem("forbidden", "Calling service has no right for this operation")
        return identity

    return checkServiceToken
