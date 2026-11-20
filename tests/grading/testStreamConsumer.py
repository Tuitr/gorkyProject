"""Redis acceptance tests for the grading worker's stream consumer."""

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from redis import Redis

from contracts.v1.events.checks import CONSUMER_GROUP, DATA_FIELD
from grading.intake import AssignmentPublished, AttemptSubmitted
from grading.stream import StreamConsumer

ExampleReader = Callable[[str], Any]


def testConsumersShareGroupAndLeaveValidEventsPending(
    gradingStream: tuple[Redis, str], contractExample: ExampleReader
) -> None:
    client, streamName = gradingStream
    assignment = contractExample("events/assignmentPublished.json")
    attempt = contractExample("events/attemptSubmitted.json")
    assignmentId = client.xadd(streamName, {DATA_FIELD: json.dumps(assignment)})
    attemptId = client.xadd(streamName, {DATA_FIELD: json.dumps(attempt)})

    first = StreamConsumer(client, "worker-one", streamName=streamName)
    published, = first.readBatch(count=1)
    assert published.messageId.encode("ascii") == assignmentId
    assert isinstance(published.event, AssignmentPublished)
    assert published.event.eventId == assignment["eventId"]

    second = StreamConsumer(client, "worker-two", streamName=streamName)
    submitted, = second.readBatch(count=1)
    assert submitted.messageId.encode("ascii") == attemptId
    assert isinstance(submitted.event, AttemptSubmitted)
    assert submitted.event.payload.checkId == attempt["payload"]["checkId"]
    assert second.readBatch() == []
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 2


def testRejectedEntriesAreLoggedAndAcknowledged(
    gradingStream: tuple[Redis, str], contractExample: ExampleReader, caplog: pytest.LogCaptureFixture
) -> None:
    client, streamName = gradingStream
    consumer = StreamConsumer(client, "worker-one", streamName=streamName)
    privateText = "private DOCX content"
    valid = contractExample("events/attemptSubmitted.json")
    invalid = valid | {"traceId": "invalid-trace", "v": 2, "payload": {"text": privateText}}
    missingId = client.xadd(streamName, {"content": privateText})
    malformedId = client.xadd(streamName, {DATA_FIELD: b"\xff" + privateText.encode("utf-8")})
    invalidId = client.xadd(streamName, {DATA_FIELD: json.dumps(invalid)})
    validId = client.xadd(streamName, {DATA_FIELD: json.dumps(valid)})

    with caplog.at_level(logging.INFO, logger="grading.stream"):
        delivery, = consumer.readBatch()

    assert delivery.messageId.encode("ascii") == validId
    pending = client.xpending_range(streamName, CONSUMER_GROUP, "-", "+", 10)
    assert [entry["message_id"] for entry in pending] == [validId]
    assert client.xlen(streamName) == 4
    for messageId, reason in [(missingId, "missing_data"), (malformedId, "malformed"), (invalidId, "invalid")]:
        assert isinstance(messageId, bytes)
        assert f"messageId={messageId.decode('ascii')} reason={reason}" in caplog.text
    assert "traceId='invalid-trace'" in caplog.text
    assert f"traceId={valid['traceId']!r}" in caplog.text
    assert privateText not in caplog.text
    assert valid["payload"]["studentId"] not in caplog.text
