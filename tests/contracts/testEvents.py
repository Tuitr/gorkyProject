import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from contracts.v1.events import checks, schema
from contracts.v1.events.checks import AssignmentPublishedEvent, AttemptSubmittedEvent, checksEventAdapter

EXAMPLES = Path(checks.__file__).parents[1] / "examples" / "events"


def readExample(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    ("fileName", "expectedType"),
    [
        ("assignmentPublished.json", AssignmentPublishedEvent),
        ("attemptSubmitted.json", AttemptSubmittedEvent),
    ],
)
def testEventExamples(fileName: str, expectedType: type) -> None:
    event = checksEventAdapter.validate_json((EXAMPLES / fileName).read_text(encoding="utf-8"))

    assert isinstance(event, expectedType)
    assert json.loads(checksEventAdapter.dump_json(event)) == readExample(fileName)


@pytest.mark.parametrize(
    "fileName",
    sorted(path.name for path in EXAMPLES.glob("*.invalid.json")),
)
def testInvalidEventExamplesAreRejected(fileName: str) -> None:
    with pytest.raises(ValidationError):
        checksEventAdapter.validate_python(readExample(fileName))


def testAttemptWithoutCheckIdIsRejected() -> None:
    example = readExample("attemptSubmitted.json")
    del example["payload"]["checkId"]

    with pytest.raises(ValidationError):
        checksEventAdapter.validate_python(example)


def testPhotoPagesPreserveOrderAndStartWithDocRef() -> None:
    example = readExample("attemptSubmitted.json")
    payload = example["payload"]
    first = payload["docRef"] | {"key": "uploads/a/first.jpg"}
    second = first | {"key": "uploads/a/second.png", "sha256": "b" * 64}
    payload.update(docRef=first, pageRefs=[first, second])
    event = checksEventAdapter.validate_python(example)
    assert json.loads(checksEventAdapter.dump_json(event)) == example

    payload["pageRefs"] = [second, first]
    with pytest.raises(ValidationError, match="docRef must match"):
        checksEventAdapter.validate_python(example)


def testAssignmentRevZeroIsRejected() -> None:
    example = readExample("assignmentPublished.json")
    example["payload"]["assignmentRev"] = 0

    with pytest.raises(ValidationError):
        checksEventAdapter.validate_python(example)


@pytest.mark.parametrize("revision", ["1", 1.0, True])
def testRevisionMustBeAnInteger(revision: object) -> None:
    example = readExample("attemptSubmitted.json")
    example["payload"]["assignmentRev"] = revision

    with pytest.raises(ValidationError):
        checksEventAdapter.validate_python(example)


@pytest.mark.parametrize("occurredAt", [1763631000, "2026-11-20T10:15:00"])
def testOccurredAtMustBeUtcRfc3339(occurredAt: object) -> None:
    example = readExample("attemptSubmitted.json") | {"occurredAt": occurredAt}

    with pytest.raises(ValidationError):
        checksEventAdapter.validate_python(example)


def testRequirementWithoutTaskNumberCoversWholeAssignment() -> None:
    example = readExample("assignmentPublished.json")
    example["payload"]["requirements"] = [{"text": "Без калькулятора"}]

    event = checksEventAdapter.validate_python(example)

    assert isinstance(event, AssignmentPublishedEvent)
    assert event.payload.requirements[0].taskNumber is None


def testCommittedJsonSchemaMatchesModels() -> None:
    committed = schema.SCHEMA_PATH.read_text(encoding="utf-8")
    assert committed == schema.renderDocument(schema.buildChecksSchema()), (
        "schemas/checks.json is stale: run `uv run python -m contracts.v1.events.schema`"
    )
