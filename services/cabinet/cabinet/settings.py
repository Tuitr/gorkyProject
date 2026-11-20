"""Settings from the environment. Defaults match deploy/compose.yaml and .env.example.

CABINET_DATABASE_URL logs in as gorky_cabinet: it owns the `cabinet` schema and nothing else
(deploy/postgres/init/10-roles.sql).
"""

import os
from dataclasses import dataclass

DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://gorky_cabinet:gorky_cabinet@127.0.0.1:15432/gorky?connect_timeout=3"
)
DB_SCHEMA = "cabinet"


@dataclass(frozen=True)
class Settings:
    databaseUrl: str = DEFAULT_DATABASE_URL

    @classmethod
    def fromEnv(cls) -> "Settings":
        return cls(databaseUrl=os.environ.get("CABINET_DATABASE_URL", DEFAULT_DATABASE_URL))
