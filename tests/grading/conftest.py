"""Service-local fixtures for the grading schema and an isolated Redis stream."""

import os
from collections.abc import Iterator
from uuid import uuid4

import pytest
from alembic import command
from redis import Redis
from redis.exceptions import ConnectionError
from sqlalchemy import Engine, create_engine, text

from contracts.v1.events.checks import STREAM_NAME
from grading.database import buildAlembicConfig
from grading.settings import Settings


@pytest.fixture(scope="session")
def migratedGradingEngine(adminEngine: Engine) -> Iterator[Engine]:
    # adminEngine installs the A1.2 roles and empty schemas before this migration.
    databaseUrl = Settings.fromEnv().databaseUrl
    command.upgrade(buildAlembicConfig(databaseUrl), "head")
    engine = create_engine(databaseUrl, hide_parameters=True)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def gradingEngine(migratedGradingEngine: Engine) -> Engine:
    with migratedGradingEngine.begin() as connection:
        connection.execute(text("TRUNCATE grading.inbox, grading.check_result_revisions, grading.check_jobs, grading.assignment_jobs"))
    return migratedGradingEngine


@pytest.fixture
def gradingStream() -> Iterator[tuple[Redis, str]]:
    redisUrl = os.environ.get("REDIS_URL", "redis://127.0.0.1:16379/0")
    streamName = f"{STREAM_NAME}.test.{uuid4().hex}"
    with Redis.from_url(redisUrl, protocol=2, socket_connect_timeout=3, socket_timeout=3) as client:
        try:
            client.ping()
        except ConnectionError:
            pytest.fail("Redis is unreachable. Run: docker compose -f deploy/compose.yaml up -d --wait", pytrace=False)
        try:
            yield client, streamName
        finally:
            client.delete(streamName)
