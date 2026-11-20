"""B1.2 acceptance tests against real PostgreSQL and Redis."""

import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from typing import Any

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import Connection, Engine, event, func, select
from sqlalchemy.orm import Session

from contracts.v1.events.checks import CONSUMER_GROUP, DATA_FIELD
from grading.inbox import SavedAssignment, SavedCheck, SavedJob
from grading.stream import StreamConsumer, StreamDelivery
from grading.tables import AssignmentJob, CheckJob, InboxEntry
from grading.worker import GradingWorker

ExampleReader = Callable[[str], Any]


def checkRows(engine: Engine) -> tuple[int, int]:
    with engine.connect() as connection:
        inboxCount = connection.scalar(select(func.count()).select_from(InboxEntry))
        checkCount = connection.scalar(select(func.count()).select_from(CheckJob))
        assert inboxCount is not None and checkCount is not None
        return inboxCount, checkCount


def testWorkerQueuesBothEventKindsByTheirKeys(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: ExampleReader
) -> None:
    client, streamName = gradingStream
    worker = GradingWorker(StreamConsumer(client, "worker-one", streamName=streamName), gradingEngine)
    published = contractExample("events/assignmentPublished.json")
    submitted = contractExample("events/attemptSubmitted.json")
    repeated = published | {"eventId": "published-repeated"}
    revised = published | {"eventId": "published-revised", "payload": published["payload"] | {"assignmentRev": 2}}
    for example in (published, submitted, repeated, revised):
        client.xadd(streamName, {DATA_FIELD: json.dumps(example)})

    assignmentId = published["payload"]["assignmentId"]
    checkId = submitted["payload"]["checkId"]
    assert worker.runBatch() == [
        SavedAssignment(assignmentId, 1, "queued"),
        SavedCheck(checkId, "queued"),
        SavedAssignment(assignmentId, 1, "queued"),
        SavedAssignment(assignmentId, 2, "queued"),
    ]
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 0
    with Session(gradingEngine) as session:
        assert session.scalar(select(func.count()).select_from(InboxEntry)) == 4
        assert session.scalar(select(func.count()).select_from(AssignmentJob)) == 2
        assert session.scalar(select(func.count()).select_from(CheckJob)) == 1
        assert session.get_one(AssignmentJob, (assignmentId, 1)).payload == published["payload"]
        assert session.get_one(CheckJob, checkId).payload == submitted["payload"]
        assert session.get_one(InboxEntry, submitted["eventId"]).traceId == submitted["traceId"]


def testDeliveryRepeatedAfterCommitReturnsStoredCheckState(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: ExampleReader, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, streamName = gradingStream
    consumer = StreamConsumer(client, "worker-one", streamName=streamName)
    worker = GradingWorker(consumer, gradingEngine)
    submitted = contractExample("events/attemptSubmitted.json")
    checkId = submitted["payload"]["checkId"]
    client.xadd(streamName, {DATA_FIELD: json.dumps(submitted)})
    delivery, = consumer.readBatch()

    def loseAck(messageId: str) -> None:
        assert messageId == delivery.messageId
        assert checkRows(gradingEngine) == (1, 1)
        raise RedisConnectionError("simulated connection loss before XACK")

    with monkeypatch.context() as scoped:
        scoped.setattr(consumer, "acknowledge", loseAck)
        with pytest.raises(RedisConnectionError, match="before XACK"):
            worker.persistDelivery(delivery)

    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 1
    with Session(gradingEngine) as session, session.begin():
        session.get_one(CheckJob, checkId).state = "running"

    assert worker.persistDelivery(delivery) == SavedCheck(checkId, "running")
    assert checkRows(gradingEngine) == (1, 1)
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 0


class CommitInterrupted(Exception):
    pass


def testRollbackBeforeCommitKeepsDeliveryPending(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: ExampleReader
) -> None:
    client, streamName = gradingStream
    consumer = StreamConsumer(client, "worker-one", streamName=streamName)
    worker = GradingWorker(consumer, gradingEngine)
    submitted = contractExample("events/attemptSubmitted.json")
    client.xadd(streamName, {DATA_FIELD: json.dumps(submitted)})
    delivery, = consumer.readBatch()

    def abortCommit(connection: Connection) -> None:
        assert connection.scalar(select(func.count()).select_from(InboxEntry)) == 1
        assert connection.scalar(select(func.count()).select_from(CheckJob)) == 1
        raise CommitInterrupted("simulated failure before PostgreSQL commit")

    event.listen(gradingEngine, "commit", abortCommit)
    try:
        with pytest.raises(CommitInterrupted, match="before PostgreSQL commit"):
            worker.persistDelivery(delivery)
    finally:
        event.remove(gradingEngine, "commit", abortCommit)

    assert checkRows(gradingEngine) == (0, 0)
    pending = client.xpending_range(streamName, CONSUMER_GROUP, "-", "+", 10)
    assert [entry["message_id"] for entry in pending] == [delivery.messageId.encode("ascii")]
    assert worker.persistDelivery(delivery) == SavedCheck(submitted["payload"]["checkId"], "queued")
    assert checkRows(gradingEngine) == (1, 1)
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 0


def testConcurrentWorkersWithDifferentEventIdsShareOneCheck(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: ExampleReader
) -> None:
    client, streamName = gradingStream
    submitted = contractExample("events/attemptSubmitted.json")
    repeated = submitted | {"eventId": "submitted-repeated"}
    client.xadd(streamName, {DATA_FIELD: json.dumps(submitted)})
    client.xadd(streamName, {DATA_FIELD: json.dumps(repeated)})
    consumers = [StreamConsumer(client, name, streamName=streamName) for name in ("worker-one", "worker-two")]
    workers = [GradingWorker(consumer, gradingEngine) for consumer in consumers]
    deliveries = [consumer.readBatch(count=1)[0] for consumer in consumers]
    barrier = Barrier(2, timeout=10)

    def persistTogether(worker: GradingWorker, delivery: StreamDelivery) -> SavedJob:
        barrier.wait()
        return worker.persistDelivery(delivery)

    with ThreadPoolExecutor(max_workers=2) as pool:
        requests = [
            pool.submit(persistTogether, worker, delivery) for worker, delivery in zip(workers, deliveries, strict=True)
        ]
        stored = [request.result(timeout=10) for request in requests]

    checkId = submitted["payload"]["checkId"]
    assert stored == [SavedCheck(checkId, "queued")] * 2
    assert checkRows(gradingEngine) == (2, 1)
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 0
