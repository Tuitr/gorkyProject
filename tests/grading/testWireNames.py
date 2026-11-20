"""Wire names of checks.v1 on both sides: the publisher models (developer 1) and the worker input (developer 2).

A name converter must not touch the event type value or quietly accept trace_id instead of traceId.
"""

import json
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from contracts.v1.events.checks import checksEventAdapter
from grading.intake import incomingEventAdapter

ExampleReader = Callable[[str], Any]

ENVELOPE_KEYS = {"v", "eventId", "type", "traceId", "occurredAt", "payload"}

EVENT_EXAMPLES = [
    ("events/assignmentPublished.json", "assignment.published"),
    ("events/attemptSubmitted.json", "attempt.submitted"),
]

SIDES: list[tuple[str, TypeAdapter[Any]]] = [
    ("publisher", checksEventAdapter),
    ("worker", incomingEventAdapter),
]


def isMissing(error: ValidationError, key: str) -> bool:
    return any(item["type"] == "missing" and item["loc"][-1] == key for item in error.errors())


@pytest.mark.parametrize(("examplePath", "eventType"), EVENT_EXAMPLES)
def testSharedExampleUsesExactWireNames(contractExample: ExampleReader, examplePath: str, eventType: str) -> None:
    example = contractExample(examplePath)

    assert set(example) == ENVELOPE_KEYS
    assert example["type"] == eventType


@pytest.mark.parametrize(("sideName", "adapter"), SIDES)
@pytest.mark.parametrize(("examplePath", "eventType"), EVENT_EXAMPLES)
def testBothSidesKeepWireNamesAndEventType(
    contractExample: ExampleReader, examplePath: str, eventType: str, sideName: str, adapter: TypeAdapter[Any]
) -> None:
    example = contractExample(examplePath)

    event = adapter.validate_json(json.dumps(example))
    wire = json.loads(adapter.dump_json(event))

    assert set(wire) == ENVELOPE_KEYS, sideName
    assert wire["type"] == eventType, sideName
    assert (wire["eventId"], wire["traceId"]) == (example["eventId"], example["traceId"]), sideName
    assert wire["occurredAt"] == example["occurredAt"], sideName


@pytest.mark.parametrize(("sideName", "adapter"), SIDES)
@pytest.mark.parametrize(("examplePath", "eventType"), EVENT_EXAMPLES)
def testSnakeCaseTraceIdIsReportedAsMissing(
    contractExample: ExampleReader, examplePath: str, eventType: str, sideName: str, adapter: TypeAdapter[Any]
) -> None:
    example = contractExample(examplePath)
    example["trace_id"] = example.pop("traceId")

    with pytest.raises(ValidationError) as caught:
        adapter.validate_json(json.dumps(example))

    assert isMissing(caught.value, "traceId"), f"{sideName} accepted {eventType} without traceId"


@pytest.mark.parametrize(("sideName", "adapter"), SIDES)
@pytest.mark.parametrize("wrongType", ["attempt_submitted", "AttemptSubmitted", "attemptSubmitted"])
def testConvertedEventTypeIsRejected(
    contractExample: ExampleReader, sideName: str, adapter: TypeAdapter[Any], wrongType: str
) -> None:
    example = contractExample("events/attemptSubmitted.json") | {"type": wrongType}

    with pytest.raises(ValidationError):
        adapter.validate_json(json.dumps(example))
