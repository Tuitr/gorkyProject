"""Fixtures shared by all tests.

contractExample reads a published contracts v1 example by its path under contracts/contracts/v1/examples,
for example contractExample("events/attemptSubmitted.json"). Provider and consumer tests read the same
committed file, so a format change cannot pass one side and break the other. Tests keep no copies of examples.

engine and sessionFactory give the registry and gateway tests PostgreSQL. Start it with:
    docker compose -f deploy/compose.yaml up -d --wait
The URL comes from REFERENCES_DATABASE_URL (default: 127.0.0.1:15432 from compose).
If the database is down, the first test reports it and the rest fail at once instead of waiting again.
Migrations run from scratch once per test session; each test starts with empty tables.
"""

import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

import contracts.v1
from references.database import buildAlembicConfig
from references.settings import DB_SCHEMA, Settings

EXAMPLES_DIR = Path(contracts.v1.__file__).parent / "examples"
TABLES = ("reference_records", "review_calls", "provider_slots")


def loadContractExample(relativePath: str) -> Any:
    path = EXAMPLES_DIR / relativePath
    if not path.is_file():
        raise FileNotFoundError(f"No published contracts v1 example {relativePath!r} in {EXAMPLES_DIR}")
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def contractExample() -> Callable[[str], Any]:
    return loadContractExample


@pytest.fixture(scope="session")
def databaseUrl() -> str:
    return Settings.fromEnv().databaseUrl


unreachableReason: list[str] = []


@pytest.fixture(scope="session")
def engine(databaseUrl: str) -> Iterator[Engine]:
    if unreachableReason:
        pytest.fail(unreachableReason[0], pytrace=False)
    engine = create_engine(databaseUrl)
    try:
        with engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{DB_SCHEMA}" CASCADE'))
    except OperationalError as error:
        unreachableReason.append(
            f"PostgreSQL is not reachable at {databaseUrl}. "
            "Run: docker compose -f deploy/compose.yaml up -d --wait\n"
            f"{error.orig}"
        )
        pytest.fail(unreachableReason[0], pytrace=False)
    command.upgrade(buildAlembicConfig(databaseUrl), "head")
    yield engine
    engine.dispose()


@pytest.fixture
def sessionFactory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    tables = ", ".join(f'"{DB_SCHEMA}".{table}' for table in TABLES)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables}"))
    yield sessionmaker(engine, expire_on_commit=False)
