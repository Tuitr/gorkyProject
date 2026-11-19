"""Settings from the environment. Defaults match deploy/compose.yaml and .env.example."""

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = "postgresql+psycopg://gorky:gorky@localhost:55432/gorky"
DB_SCHEMA = "refs"


@dataclass(frozen=True)
class Settings:
    databaseUrl: str

    @classmethod
    def fromEnv(cls) -> "Settings":
        return cls(databaseUrl=os.environ.get("REFERENCES_DATABASE_URL", DEFAULT_DATABASE_URL))
