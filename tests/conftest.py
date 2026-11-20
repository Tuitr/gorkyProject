"""Fixtures shared by all tests.

contractExample reads a published contracts v1 example by its path under contracts/contracts/v1/examples,
for example contractExample("events/attemptSubmitted.json"). Provider and consumer tests read the same
committed file, so a format change cannot pass one side and break the other. Tests keep no copies of examples.

The database fixtures need PostgreSQL. Start it with:
    docker compose -f deploy/compose.yaml up -d --wait
Once per test session, as the stand administrator (DATABASE_URL), the service schemas are dropped and
deploy/postgres/init/10-roles.sql recreates the roles and schemas; then every service runs its own Alembic
migrations under its own role (CABINET_DATABASE_URL, REFERENCES_DATABASE_URL). Each test starts with empty
tables. If the database is down, the first test reports it and the rest fail at once instead of waiting again.
"""

import json
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

import cabinet.database
import cabinet.settings
import contracts.v1
import references.database
import references.settings

EXAMPLES_DIR = Path(contracts.v1.__file__).parent / "examples"
ROLES_SQL = Path(__file__).parents[1] / "deploy" / "postgres" / "init" / "10-roles.sql"
ADMIN_DATABASE_URL = "postgresql+psycopg://gorky:gorky@127.0.0.1:15432/gorky?connect_timeout=3"
START_HINT = "Run: docker compose -f deploy/compose.yaml up -d --wait"
SERVICE_SCHEMAS = ("cabinet", "grading", references.settings.DB_SCHEMA)
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
def handoffDocuments() -> tuple[bytes, bytes]:
    examples = EXAMPLES_DIR / "handoff"
    return (examples / "variant.docx").read_bytes(), (examples / "submission.docx").read_bytes()


@dataclass(frozen=True)
class ServiceMigrations:
    schema: str
    databaseUrl: str
    alembicConfig: Callable[[str], Config]


def serviceMigrations() -> list[ServiceMigrations]:
    """Every service with an Alembic environment; grading joins when developer 2 adds one."""
    return [
        ServiceMigrations(
            cabinet.settings.DB_SCHEMA, cabinet.settings.Settings.fromEnv().databaseUrl, cabinet.database.buildAlembicConfig
        ),
        ServiceMigrations(
            references.settings.DB_SCHEMA,
            references.settings.Settings.fromEnv().databaseUrl,
            references.database.buildAlembicConfig,
        ),
    ]


def applyRoles(engine: Engine) -> None:
    """Run deploy/postgres/init/10-roles.sql as the administrator, the way PostgreSQL runs it on a new volume."""
    with engine.connect() as connection:
        driverConnection = connection.connection.driver_connection
        assert driverConnection is not None
        driverConnection.execute(ROLES_SQL.read_text(encoding="utf-8"))
        driverConnection.commit()


@pytest.fixture(scope="session")
def rolesScript() -> Callable[[Engine], None]:
    """applyRoles, for tests that run the roles script again on a prepared stand."""
    return applyRoles


@pytest.fixture(scope="session")
def adminDatabaseUrl() -> str:
    return os.environ.get("DATABASE_URL", ADMIN_DATABASE_URL)


unreachableReason: list[str] = []


@pytest.fixture(scope="session")
def adminEngine(adminDatabaseUrl: str) -> Iterator[Engine]:
    """The stand administrator, after the session reset: roles, empty schemas, every migration at head."""
    if unreachableReason:
        pytest.fail(unreachableReason[0], pytrace=False)
    engine = create_engine(adminDatabaseUrl)
    try:
        with engine.begin() as connection:
            for schema in SERVICE_SCHEMAS:
                connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
    except OperationalError as error:
        unreachableReason.append(f"PostgreSQL is not reachable at {adminDatabaseUrl}. {START_HINT}\n{error.orig}")
        pytest.fail(unreachableReason[0], pytrace=False)
    applyRoles(engine)
    for service in serviceMigrations():
        command.upgrade(service.alembicConfig(service.databaseUrl), "head")
    yield engine
    engine.dispose()


@pytest.fixture(scope="session")
def databaseUrl() -> str:
    """The reference service's own role (gorky_references)."""
    return references.settings.Settings.fromEnv().databaseUrl


@pytest.fixture(scope="session")
def engine(adminEngine: Engine, databaseUrl: str) -> Iterator[Engine]:
    engine = create_engine(databaseUrl)
    yield engine
    engine.dispose()


@pytest.fixture
def sessionFactory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    tables = ", ".join(f'"{references.settings.DB_SCHEMA}".{table}' for table in TABLES)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables}"))
    yield sessionmaker(engine, expire_on_commit=False)
