"""A6.1: the manifest of the annotated mini dataset and the validator that guards its links."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from contracts.v1.dataset import manifest as manifestModule
from contracts.v1.dataset import schema
from contracts.v1.dataset.manifest import AssignmentRef, DatasetCase, DatasetManifest

EXAMPLES = Path(manifestModule.__file__).parents[1] / "examples" / "dataset"
INVALID = sorted(path.name for path in EXAMPLES.glob("*.invalid.json"))


def readExample(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def modelFor(fileName: str) -> type[BaseModel]:
    """case*.json holds one record, manifest*.json a whole manifest."""
    return DatasetCase if fileName.startswith("case") else DatasetManifest


@pytest.mark.parametrize(("fileName", "model"), [("case.json", DatasetCase), ("manifest.json", DatasetManifest)])
def testValidExamplesRoundTrip(fileName: str, model: type[BaseModel]) -> None:
    parsed = model.model_validate(readExample(fileName))

    assert json.loads(parsed.model_dump_json()) == readExample(fileName)


def testMinimalRecordIsTheFirstCaseOfTheManifest() -> None:
    assert readExample("manifest.json")["cases"][0] == readExample("case.json")


@pytest.mark.parametrize("fileName", INVALID)
def testInvalidExamplesAreRejected(fileName: str) -> None:
    with pytest.raises(ValidationError):
        modelFor(fileName).model_validate(readExample(fileName))


def errorsOf(fileName: str) -> list[tuple[tuple[str | int, ...], str, str]]:
    with pytest.raises(ValidationError) as caught:
        modelFor(fileName).model_validate(readExample(fileName))
    return [(error["loc"], error["type"], error["msg"]) for error in caught.value.errors()]


def testValidatorTellsAMissingCaseIdFromAnIssueOnAnUnknownStep() -> None:
    assert errorsOf("caseWithoutCaseId.invalid.json") == [(("caseId",), "missing", "Field required")]
    assert errorsOf("caseIssueOnUnknownStep.invalid.json") == [
        ((), "value_error", "Value error, case vectorLength68Consequence: issue i1 points to unknown step s9")
    ]


@pytest.mark.parametrize(
    ("fileName", "message"),
    [
        ("caseCauseCycle.invalid.json", "issue i1 is in a cycle of causes"),
        ("caseSkippedWithSteps.invalid.json", "a skipped task has no steps and no issues"),
        ("manifestDuplicateCaseId.invalid.json", "caseId repeats: vectorLength34Correct"),
        ("manifestClosedSharesSourceTask.invalid.json", "share a source task with the closed split: vectorLength34Correct"),
    ],
)
def testLinkErrorsNameTheCase(fileName: str, message: str) -> None:
    assert any(message in text for _, _, text in errorsOf(fileName))


def testCauseMustBeAnIssueOfTheSameCase() -> None:
    record = readExample("case.json") | {
        "expectedSteps": [{"stepId": "s1", "markdown": "x"}],
        "expectedIssues": [{"issueId": "i1", "stepId": "s1", "category": "answer", "causedByIssueId": "i7"}],
    }

    with pytest.raises(ValidationError, match="caused by unknown issue i7"):
        DatasetCase.model_validate(record)


def testOneSubmissionFileHasOneHash() -> None:
    manifest = readExample("manifest.json")
    manifest["cases"][1]["submissionRef"]["sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="submissions/student1.docx differs from another case"):
        DatasetManifest.model_validate(manifest)


def testStatementAndKeyAreSeparateFields() -> None:
    """The student sees the statement; the key lives only in expectedReference."""
    case = DatasetCase.model_validate(readExample("case.json"))

    assert case.expectedReference is not None
    assert str(case.expectedReference.value["exact"]) not in case.assignmentRef.statement
    assert not {"expectedReference", "schemaId", "value"} & set(AssignmentRef.model_fields)


def testCommittedJsonSchemaMatchesModels() -> None:
    committed = schema.SCHEMA_PATH.read_text(encoding="utf-8")
    assert committed == schema.renderDocument(schema.buildDatasetSchema()), (
        "schemas/dataset.json is stale: run `uv run python -m contracts.v1.dataset.schema`"
    )
