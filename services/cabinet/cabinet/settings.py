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
    loginMaxFailures: int = 5
    """Failed logins of one login within loginWindowS before it is blocked for loginWindowS."""
    clientMaxFailures: int = 20
    """Failed logins from one client address (any logins) before that address is blocked."""
    loginWindowS: int = 900

    @classmethod
    def fromEnv(cls) -> "Settings":
        return cls(
            databaseUrl=os.environ.get("CABINET_DATABASE_URL", DEFAULT_DATABASE_URL),
            loginMaxFailures=int(os.environ.get("CABINET_LOGIN_MAX_FAILURES", cls.loginMaxFailures)),
            clientMaxFailures=int(os.environ.get("CABINET_CLIENT_MAX_FAILURES", cls.clientMaxFailures)),
            loginWindowS=int(os.environ.get("CABINET_LOGIN_WINDOW_S", cls.loginWindowS)),
        )
