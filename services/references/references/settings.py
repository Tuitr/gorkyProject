"""Settings from the environment. Defaults match deploy/compose.yaml and .env.example.

REFERENCES_SERVICE_TOKENS: {"<token>": {"serviceName": "grading", "rights": ["references.request"]}}
REFERENCES_PROVIDER: fixture (recorded references only, the default) or generating (fixtures first, then a solver
generated for the task by references/solvers; needs the solver-builder process). Nothing switches it implicitly.
"""

import os
from dataclasses import dataclass
from typing import Literal

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://gorky_references:gorky_references@127.0.0.1:15432/gorky?connect_timeout=3"
)
"""gorky_references owns the `refs` schema and nothing else (deploy/postgres/init/10-roles.sql)."""
DB_SCHEMA = "refs"
ProviderName = Literal["fixture", "generating"]
PROVIDERS: dict[str, ProviderName] = {"fixture": "fixture", "generating": "generating"}
DEFAULT_BUILD_DEADLINE_S = 900.0
"""How long one solver build may take from the first request, all model calls and sandbox runs included."""
DEFAULT_BUILD_POLL_S = 10
"""retryAfterS of a reference that waits for a solver build."""


class SettingsError(ValueError):
    """The environment does not choose a usable provider. Raised at startup."""


@dataclass(frozen=True)
class Settings:
    databaseUrl: str
    serviceTokensJson: str = "{}"
    fixturesPath: str | None = None
    provider: ProviderName = "fixture"
    buildDeadlineS: float = DEFAULT_BUILD_DEADLINE_S
    buildPollS: int = DEFAULT_BUILD_POLL_S

    @classmethod
    def fromEnv(cls) -> "Settings":
        value = os.environ.get("REFERENCES_PROVIDER", "fixture").strip()
        provider = PROVIDERS.get(value)
        if provider is None:
            raise SettingsError(f"REFERENCES_PROVIDER must be one of {', '.join(PROVIDERS)}, got {value!r}")
        return cls(
            databaseUrl=os.environ.get("REFERENCES_DATABASE_URL", DEFAULT_DATABASE_URL),
            serviceTokensJson=os.environ.get("REFERENCES_SERVICE_TOKENS", "{}"),
            fixturesPath=os.environ.get("REFERENCES_FIXTURES_PATH"),
            provider=provider,
            buildDeadlineS=positive("SOLVER_BUILD_DEADLINE_S", DEFAULT_BUILD_DEADLINE_S),
            buildPollS=int(positive("REFERENCES_BUILD_POLL_S", DEFAULT_BUILD_POLL_S)),
        )


def positive(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0
    if value < 1:
        raise SettingsError(f"{name} must be a number of seconds, at least 1, got {raw!r}")
    return value
