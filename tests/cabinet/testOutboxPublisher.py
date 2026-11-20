"""Check Redis delivery and retry against isolated outbox rows."""

import json
import uuid
from collections.abc import Iterator

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from cabinet.database import Base
from cabinet.outbox import OutboxRecord, enqueueEvent
from cabinet.outboxpublisher import IDLE_DELAY_S, RETRY_DELAY_S, appendToStream, publishNext, runPublisher
from cabinet.settings import DB_SCHEMA, Settings
from contracts.v1.events.checks import (
    DATA_FIELD,
    STREAM_NAME,
    AssignmentPublishedEvent,
    AssignmentPublishedPayload,
    DocRef,
    checksEventAdapter,
)
from shared.clock import ManualClock


@pytest.fixture
def publisherSessions(adminEngine: Engine) -> Iterator[sessionmaker[Session]]:
    # The Compose publisher continues to serve the normal cabinet schema.
    schema = f"publisher_test_{uuid.uuid4().hex}"
    isolated = adminEngine.execution_options(schema_translate_map={DB_SCHEMA: schema})
    with isolated.begin() as connection:
        connection.execute(CreateSchema(schema))
        Base.metadata.tables[f"{DB_SCHEMA}.outbox"].create(connection)
    try:
        yield sessionmaker(isolated)
    finally:
        with isolated.begin() as connection:
            connection.execute(DropSchema(schema, cascade=True))


def testPublisherRetriesWithoutChangingTheEvent(publisherSessions: sessionmaker[Session]) -> None:
    class Finished(Exception):
        pass

    class BoundedClock(ManualClock):
        def sleep(self, seconds: float) -> None:
            super().sleep(seconds)
            if len(self.sleeps) == 3:
                raise Finished()

    clock = BoundedClock()
    event = AssignmentPublishedEvent(
        v=1, eventId=str(uuid.uuid4()), type="assignment.published",
        traceId="retry-test", occurredAt=clock.now(),
        payload=AssignmentPublishedPayload(
            assignmentId=str(uuid.uuid4()), assignmentRev=1,
            requirements=[], docRef=DocRef(bucket="sources", key="retry-test.docx", sha256="0" * 64),
        ),
    )
    with publisherSessions.begin() as db:
        enqueueEvent(db, event)

    with Redis.from_url(Settings.fromEnv().redisUrl, socket_connect_timeout=2, socket_timeout=2) as redis:
        sends: list[str] = []

        def send(data: str) -> None:
            with publisherSessions() as db:
                assert db.scalars(select(OutboxRecord)).one().publishedAt is None
            sends.append(data)
            if len(sends) == 1:
                raise RedisConnectionError("Redis is unavailable")
            appendToStream(redis, data)
            if len(sends) == 2:
                raise RedisTimeoutError("Response lost after XADD")

        testEntries = []
        try:
            with pytest.raises(Finished):
                runPublisher(publisherSessions, send, clock)
            with publisherSessions() as db:
                confirmed = db.scalars(select(OutboxRecord)).one()
                assert confirmed.eventId == event.eventId
                assert confirmed.publishedAt is not None
            assert clock.sleeps == [RETRY_DELAY_S, RETRY_DELAY_S, IDLE_DELAY_S]
            assert publishNext(publisherSessions, send, clock) is False
            assert len(sends) == 3 and len(set(sends)) == 1

            field = DATA_FIELD.encode()
            testEntries = [
                (entryId, fields[field])
                for entryId, fields in redis.xrange(STREAM_NAME)
                if field in fields and json.loads(fields[field]).get("eventId") == event.eventId
            ]
            assert len(testEntries) == 2
            assert testEntries[0][0] != testEntries[1][0]
            assert all(data == sends[0].encode() for _, data in testEntries)
            assert all(checksEventAdapter.validate_json(data) == event for _, data in testEntries)
        finally:
            if testEntries:
                redis.xdel(STREAM_NAME, *(entryId for entryId, _ in testEntries))
