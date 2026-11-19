"""Settings from the environment. Defaults match deploy/compose.yaml and .env.example.

REFERENCES_SERVICE_TOKENS: {"<token>": {"serviceName": "grading", "rights": ["references.request"]}}
"""

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "postgresql+psycopg://gorky:gorky@127.0.0.1:15432/gorky?connect_timeout=3"
DB_SCHEMA = "refs"


@dataclass(frozen=True)
class Settings:
    databaseUrl: str
    serviceTokensJson: str = "{}"
    fixturesPath: str | None = None

    @classmethod
    def fromEnv(cls) -> "Settings":
        return cls(
            databaseUrl=os.environ.get("REFERENCES_DATABASE_URL", DEFAULT_DATABASE_URL),
            serviceTokensJson=os.environ.get("REFERENCES_SERVICE_TOKENS", "{}"),
            fixturesPath=os.environ.get("REFERENCES_FIXTURES_PATH"),
        )
