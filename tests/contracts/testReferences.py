import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from contracts.v1 import openapi
from contracts.v1.references.reference import (
    ReferencePending,
    ReferenceReady,
    ReferenceRequest,
    ReferenceUnsupported,
    ReferenceValidationFailed,
    referenceResultAdapter,
)

EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"


def readExample(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def testReferenceRequestExample() -> None:
    request = ReferenceRequest.model_validate(readExample("request.json"))

    assert request.assignmentRev == 1
    assert request.familyHint is None


@pytest.mark.parametrize(
    ("fileName", "expectedType"),
    [
        ("ready.json", ReferenceReady),
        ("readyZero.json", ReferenceReady),
        ("pending.json", ReferencePending),
        ("unsupported.json", ReferenceUnsupported),
        ("validationFailed.json", ReferenceValidationFailed),
    ],
)
def testReferenceResultExamples(fileName: str, expectedType: type) -> None:
    result = referenceResultAdapter.validate_python(readExample(fileName))

    assert isinstance(result, expectedType)
    assert json.loads(referenceResultAdapter.dump_json(result)) == readExample(fileName)


def testZeroIsAValidReadyValue() -> None:
    result = referenceResultAdapter.validate_python(readExample("readyZero.json"))

    assert isinstance(result, ReferenceReady)
    assert result.value == {"exact": "0"}


@pytest.mark.parametrize(
    "fileName",
    sorted(path.name for path in EXAMPLES.glob("*.invalid.json")),
)
def testInvalidReferenceExamplesAreRejected(fileName: str) -> None:
    with pytest.raises(ValidationError):
        referenceResultAdapter.validate_python(readExample(fileName))


def testUnknownSchemaIdIsRejected() -> None:
    example = readExample("ready.json") | {"schemaId": "vector.v9"}

    with pytest.raises(ValidationError):
        referenceResultAdapter.validate_python(example)


def testCommittedOpenApiMatchesModels() -> None:
    for fileName, document in openapi.buildAll().items():
        committed = (openapi.OPENAPI_DIR / fileName).read_text(encoding="utf-8")
        assert committed == openapi.renderDocument(document), (
            f"{fileName} is stale: run `uv run python -m contracts.v1.openapi`"
        )
