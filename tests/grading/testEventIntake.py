"""Junction developer 1 -> developer 2: the same checks.v1 example read by the publisher schema and the worker."""

import json
from collections.abc import Callable
from typing import Any

import pytest
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from contracts.v1.events.schema import SCHEMA_PATH
from grading.intake import AssignmentPublished, AttemptSubmitted, readEvent

ExampleReader = Callable[[str], Any]


@pytest.fixture(scope="module")
def publisherSchema() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def testAttemptExampleGivesSameCheckIdToBothSides(
    contractExample: ExampleReader, publisherSchema: Draft202012Validator
) -> None:
    example = contractExample("events/attemptSubmitted.json")

    publisherSchema.validate(example)
    event = readEvent(json.dumps(example))

    assert isinstance(event, AttemptSubmitted)
    assert event.payload.checkId == example["payload"]["checkId"]
    assert event.eventId == example["eventId"]
    assert event.traceId == example["traceId"]


def testAssignmentExampleIsReadByBothSides(
    contractExample: ExampleReader, publisherSchema: Draft202012Validator
) -> None:
    example = contractExample("events/assignmentPublished.json")

    publisherSchema.validate(example)
    event = readEvent(json.dumps(example))

    assert isinstance(event, AssignmentPublished)
    assert event.payload.assignmentRev == example["payload"]["assignmentRev"]
    assert [requirement.taskNumber for requirement in event.payload.requirements] == [
        requirement.get("taskNumber") for requirement in example["payload"]["requirements"]
    ]


def testWorkerReadsAttemptWithNewOptionalFields(contractExample: ExampleReader) -> None:
    example = contractExample("events/attemptSubmitted.json")
    example["schemaHint"] = "added by a newer publisher"
    example["payload"]["submittedVia"] = "upload"
    example["payload"]["docRef"]["sizeBytes"] = 18234

    event = readEvent(json.dumps(example))

    assert isinstance(event, AttemptSubmitted)
    assert event.payload.checkId == example["payload"]["checkId"]


@pytest.mark.parametrize(
    "missingKey", ["v", "eventId", "type", "traceId", "occurredAt", "payload"]
)
def testWorkerRejectsAttemptWithoutRequiredEnvelopeKey(
    contractExample: ExampleReader, missingKey: str
) -> None:
    example = contractExample("events/attemptSubmitted.json")
    del example[missingKey]

    with pytest.raises(ValidationError):
        readEvent(json.dumps(example))


def testWorkerRejectsAttemptWithoutCheckId(contractExample: ExampleReader) -> None:
    example = contractExample("events/attemptSubmitted.json")
    del example["payload"]["checkId"]

    with pytest.raises(ValidationError):
        readEvent(json.dumps(example))


def testUnknownExampleNameFailsLoudly(contractExample: ExampleReader) -> None:
    with pytest.raises(FileNotFoundError):
        contractExample("events/noSuchEvent.json")
