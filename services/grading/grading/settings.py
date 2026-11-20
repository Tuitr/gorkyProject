"""Database settings for the grading service role from A1.2."""

import os
from dataclasses import dataclass

DB_SCHEMA = "grading"
DEFAULT_DATABASE_URL = "postgresql+psycopg://gorky_grading:gorky_grading@127.0.0.1:15432/gorky?connect_timeout=3"


@dataclass(frozen=True)
class Settings:
    databaseUrl: str = DEFAULT_DATABASE_URL

    @classmethod
    def fromEnv(cls) -> "Settings":
        return cls(databaseUrl=os.environ.get("GRADING_DATABASE_URL", DEFAULT_DATABASE_URL))
