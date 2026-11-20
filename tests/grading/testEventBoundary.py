"""Boundary of the worker input: raw stream data in, a validated event or a managed rejection out."""

import json
import sys
from collections.abc import Callable
from typing import Any

import pytest

from grading.intake import (
    AttemptSubmitted,
    EventRejected,
    IncomingEvent,
    InvalidEvent,
    MalformedEvent,
    readEvent,
)

ExampleReader = Callable[[str], Any]


def handOver(data: str | bytes, handled: list[IncomingEvent]) -> None:
    """Stands in for the worker loop: the handler sees only what readEvent returned."""
    handled.append(readEvent(data))


def testCorruptedJsonIsMalformedBeforeHandling() -> None:
    handled: list[IncomingEvent] = []

    with pytest.raises(MalformedEvent) as caught:
        handOver(b'{"v": 1, "eventId": ', handled)

    assert caught.value.reason == "malformed"
    assert handled == []


def testJsonWithoutEventIdIsInvalidBeforeHandling(contractExample: ExampleReader) -> None:
    example = contractExample("events/attemptSubmitted.json")
    del example["eventId"]
    handled: list[IncomingEvent] = []

    with pytest.raises(InvalidEvent) as caught:
        handOver(json.dumps(example), handled)

    assert caught.value.reason == "invalid"
    assert caught.value.problems == ("attempt.submitted.eventId: missing",)
    assert caught.value.traceId == example["traceId"]
    assert handled == []


def testReadAndSchemaFailuresAreDistinguishable(contractExample: ExampleReader) -> None:
    withoutEventId = contractExample("events/attemptSubmitted.json")
    del withoutEventId["eventId"]

    with pytest.raises(EventRejected) as corrupted:
        readEvent("{not json")
    with pytest.raises(EventRejected) as invalid:
        readEvent(json.dumps(withoutEventId))

    assert type(corrupted.value) is MalformedEvent
    assert type(invalid.value) is InvalidEvent


@pytest.mark.parametrize(
    "data",
    [b"\xff\xfe{}", b"", "   ", "[1, 2]", '"attempt.submitted"', "null"],
)
def testUnreadableDataIsMalformed(data: str | bytes) -> None:
    with pytest.raises(MalformedEvent):
        readEvent(data)


@pytest.mark.skipif(sys.get_int_max_str_digits() == 0, reason="Python's integer conversion limit is disabled")
def testOversizedJsonIntegerIsMalformed() -> None:
    data = '{"number":' + "1" * (sys.get_int_max_str_digits() + 1) + "}"
    with pytest.raises(MalformedEvent):
        readEvent(data)


def testBooleanVersionIsInvalid(contractExample: ExampleReader) -> None:
    example = contractExample("events/attemptSubmitted.json") | {"v": True}
    with pytest.raises(InvalidEvent):
        readEvent(json.dumps(example))


def testUnknownEventTypeIsInvalid(contractExample: ExampleReader) -> None:
    example = contractExample("events/attemptSubmitted.json") | {"type": "attempt.deleted"}

    with pytest.raises(InvalidEvent) as caught:
        readEvent(json.dumps(example))

    assert caught.value.problems == ("event: union_tag_invalid",)


def testRejectionDoesNotEchoInputValues(contractExample: ExampleReader) -> None:
    example = contractExample("events/attemptSubmitted.json")
    example["payload"]["studentId"] = "Иванов Пётр 7Б"
    example["payload"]["assignmentRev"] = "draft"
    del example["eventId"]

    with pytest.raises(InvalidEvent) as caught:
        readEvent(json.dumps(example))

    assert "Иванов" not in str(caught.value)
    assert "draft" not in str(caught.value)
    assert set(caught.value.problems) == {
        "attempt.submitted.eventId: missing",
        "attempt.submitted.payload.assignmentRev: int_type",
    }


@pytest.mark.parametrize("asBytes", [True, False])
def testValidExampleReachesHandlerAsTypedEvent(contractExample: ExampleReader, asBytes: bool) -> None:
    text = json.dumps(contractExample("events/attemptSubmitted.json"))
    handled: list[IncomingEvent] = []

    handOver(text.encode("utf-8") if asBytes else text, handled)

    assert len(handled) == 1
    event = handled[0]
    assert isinstance(event, AttemptSubmitted)
    assert event.type == "attempt.submitted"
    assert event.payload.checkId == "c1"
