"""C6.2: metadata of a future solver version; only a fixture may go without solverVersion."""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from contracts.v1 import openapi
from contracts.v1.references.reference import ReferenceReady, referenceResultAdapter
from contracts.v1.references.solverartifact import SolverArtifact, metadataKey, versionPrefix

EXAMPLES = Path(openapi.__file__).parent / "examples" / "solvers"


def readExample(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


@pytest.mark.parametrize("fileName", ["generated.json", "manual.json"])
def testSyntheticArtifactsRoundTrip(fileName: str) -> None:
    artifact = SolverArtifact.model_validate(readExample(fileName))

    assert json.loads(artifact.model_dump_json()) == readExample(fileName)
    for stored in (artifact.code, artifact.tests):
        assert stored.key.startswith(versionPrefix(artifact.solverVersion))
    assert metadataKey(artifact.solverVersion) == f"references/solvers/{artifact.solverVersion}/metadata.json"


def testGeneratedArtifactKeepsItsTrace() -> None:
    artifact = SolverArtifact.model_validate(readExample("generated.json"))

    assert artifact.source == "generated"
    assert artifact.generation is not None
    assert artifact.generation.modelId == "GigaChat-Max"


@pytest.mark.parametrize("fileName", sorted(path.name for path in EXAMPLES.glob("*.invalid.json")))
def testInvalidArtifactsAreRejected(fileName: str) -> None:
    with pytest.raises(ValidationError):
        SolverArtifact.model_validate(readExample(fileName))


@pytest.mark.parametrize(
    "changes",
    [
        {"code": {"key": "references/solvers/vectorLength.v1/../vectorLength.v2/solver.py"}},
        {"code": {"key": "references/solvers/vectorLength.v1/metadata.json"}},
        {"code": {"key": "references/solvers/vectorLength.v2/solver.py"}},
        {"tests": {"key": "references/solvers/vectorLength.v1/solver.py"}},
        {"code": {"sha256": "abc"}},
        {"scope": {"schemaId": "vector.v9"}},
        {"solverVersion": "vectorLength.v0"},
        {"generation": {"modelId": "GigaChat-Max", "authorInputSha256": "0" * 64, "statement": "Длина вектора"}},
    ],
    ids=[
        "dotDot",
        "overwritesMetadata",
        "otherVersion",
        "sameObject",
        "shortHash",
        "unknownSchema",
        "versionZero",
        "textInTrace",
    ],
)
def testArtifactRules(changes: dict[str, Any]) -> None:
    body = readExample("generated.json")
    for field, value in changes.items():
        body[field] = body[field] | value if isinstance(value, dict) and isinstance(body[field], dict) else value

    with pytest.raises(ValidationError):
        SolverArtifact.model_validate(body)


def testOnlyAFixtureReferenceMayHaveNoSolverVersion() -> None:
    fixture = referenceResultAdapter.validate_python(readReference("ready.json"))

    assert isinstance(fixture, ReferenceReady)
    assert fixture.solverVersion is None
    for source in ("generated", "manual"):
        with pytest.raises(ValidationError):
            ReferenceReady.model_validate(readReference("ready.json") | {"source": source})


def testReadyReferenceNamesAPublishedVersion() -> None:
    reference = ReferenceReady.model_validate(readReference("readyGenerated.json"))
    artifact = SolverArtifact.model_validate(readExample("generated.json"))

    assert reference.solverVersion == artifact.solverVersion
    with pytest.raises(ValidationError):
        ReferenceReady.model_validate(readReference("readyGenerated.json") | {"solverVersion": "vectorLength.latest"})


@pytest.mark.parametrize(
    ("familyId", "constraints", "covered"),
    [
        ("vectorLength", {}, True),
        ("vectorLength", {"answerForm": "decimal"}, True),
        ("vectorLength", {"answerForm": "fraction"}, False),
        ("vectorLength", {"precision": 2}, False),
        ("vectorMidpoint", {}, False),
        (None, {}, False),
    ],
)
def testScopeCoversOnlyListedConstraints(familyId: str | None, constraints: dict[str, Any], covered: bool) -> None:
    scope = SolverArtifact.model_validate(readExample("generated.json")).scope

    assert scope.covers(familyId, constraints) is covered


def readReference(name: str) -> Any:
    return json.loads((EXAMPLES.parent / "references" / name).read_text(encoding="utf-8"))
