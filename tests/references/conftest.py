"""PostgreSQL for registry tests.

Start it with:  docker compose -f deploy/compose.yaml up -d postgres
The URL comes from REFERENCES_DATABASE_URL (default: the compose port 55432).
Migrations run from scratch once per test session; each test starts with an empty table.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from references.settings import DB_SCHEMA, Settings

ALEMBIC_INI = Path(__file__).parents[2] / "services" / "references" / "alembic.ini"


def buildAlembicConfig(databaseUrl: str) -> Config:
    config = Config(str(ALEMBIC_INI))
    config.attributes["databaseUrl"] = databaseUrl
    return config


@pytest.fixture(scope="session")
def databaseUrl() -> str:
    return Settings.fromEnv().databaseUrl


@pytest.fixture(scope="session")
def engine(databaseUrl: str) -> Iterator[Engine]:
    engine = create_engine(databaseUrl)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{DB_SCHEMA}" CASCADE'))
    except OperationalError as error:
        pytest.fail(
            f"PostgreSQL is not reachable at {databaseUrl}. "
            "Run: docker compose -f deploy/compose.yaml up -d postgres\n"
            f"{error.orig}"
        )
    command.upgrade(buildAlembicConfig(databaseUrl), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def sessionFactory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    with engine.begin() as connection:
        connection.execute(text(f'TRUNCATE "{DB_SCHEMA}".reference_records'))
    yield sessionmaker(engine, expire_on_commit=False)
