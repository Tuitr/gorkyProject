"""C6.1: the registry talks to any reference provider through one typed interface."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from references.providers.base import (
    PendingOutcome,
    ProviderOutcome,
    ProviderTask,
    ReadyOutcome,
    ReferenceProvider,
    RefusedOutcome,
)
from references.registry.main import createApp
from references.settings import Settings
from shared.clock import ManualClock

TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["references.request"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/internal/v1/references"
EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"


def example(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    return loaded


def buildClient(
    sessionFactory: sessionmaker[Session], provider: ReferenceProvider | None = None, clock: ManualClock | None = None
) -> TestClient:
    settings = Settings(databaseUrl="unused", serviceTokensJson=TOKENS)
    return TestClient(createApp(settings, sessionFactory=sessionFactory, provider=provider, clock=clock))


class FakeSolver:
    """A future provider: pending on start, pending on the first check, then a generated reference."""

    def __init__(self, *checks: ProviderOutcome) -> None:
        self.checks = list(checks)
        self.tasks: list[ProviderTask] = []
        self.startCalls = 0

    def start(self, task: ProviderTask) -> ProviderOutcome:
        self.tasks.append(task)
        self.startCalls += 1
        return PendingOutcome(retryAfterS=5)

    def check(self, task: ProviderTask) -> ProviderOutcome:
        self.tasks.append(task)
        return self.checks.pop(0)


GENERATED = ReadyOutcome(source="generated", schemaId="scalar.v1", value={"exact": "13"}, solverVersion="vectorLength.v1")


@pytest.mark.parametrize(
    ("statement", "expected"),
    [("Длина вектора (3; 4)", "ready.json"), ("Длина вектора (7; 7)", "unsupported.json")],
    ids=["knownFixture", "unknownFixture"],
)
def testFixtureAnswersKeepTheirShapeAndId(sessionFactory: sessionmaker[Session], statement: str, expected: str) -> None:
    client = buildClient(sessionFactory)
    body = example("request.json") | {"statement": statement}

    first = client.post(URL, json=body, headers=AUTH)
    again = client.post(URL, json=body, headers=AUTH)

    assert first.status_code == again.status_code == 200
    assert again.json() == first.json()
    assert first.json() == example(expected) | {"referenceId": first.json()["referenceId"]}


def testFutureSolverPlugsInThroughTheSameApi(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    solver = FakeSolver(PendingOutcome(retryAfterS=5), GENERATED)
    client = buildClient(sessionFactory, solver, clock)
    body = example("request.json") | {"statement": "Длина вектора (5; 12)", "familyHint": "vectorLength"}

    posted = client.post(URL, json=body, headers=AUTH)
    referenceId = posted.json()["referenceId"]
    clock.advance(5)
    stillPending = client.get(f"{URL}/{referenceId}", headers=AUTH)
    clock.advance(5)
    ready = client.get(f"{URL}/{referenceId}", headers=AUTH)
    repeated = client.post(URL, json=body, headers=AUTH)

    assert posted.status_code == 202
    assert stillPending.json() == {"referenceId": referenceId, "state": "pending", "retryAfterS": 5}
    assert ready.json() == {
        "referenceId": referenceId,
        "state": "ready",
        "source": "generated",
        "schemaId": "scalar.v1",
        "value": {"exact": "13"},
        "solverVersion": "vectorLength.v1",
    }
    assert repeated.json() == ready.json()
    assert solver.startCalls == 1
    assert len(solver.tasks) == 3


def testProviderRefusalIsStoredWithItsReason(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    solver = FakeSolver(RefusedOutcome(state="validation_failed", reasonCode="independent_tests_failed"))
    client = buildClient(sessionFactory, solver, clock)

    referenceId = client.post(URL, json=example("request.json"), headers=AUTH).json()["referenceId"]
    clock.advance(5)
    final = client.get(f"{URL}/{referenceId}", headers=AUTH)

    assert final.json() == example("validationFailed.json") | {"referenceId": referenceId}


def testProviderSeesOnlyTheAuthorContext(sessionFactory: sessionmaker[Session]) -> None:
    solver = FakeSolver()
    body = example("request.json") | {"constraints": {"answerForm": "exact"}, "familyHint": "vectorLength"}

    buildClient(sessionFactory, solver).post(URL, json=body, headers=AUTH)

    (task,) = solver.tasks
    assert task.model_dump() == {
        "assignmentRev": 1,
        "statement": "Длина вектора (3; 4)",
        "constraints": {"answerForm": "exact"},
        "familyHint": "vectorLength",
    }
    with pytest.raises(ValidationError):
        ProviderTask.model_validate(task.model_dump() | {"studentAnswer": "5"})


@pytest.mark.parametrize(
    "outcome",
    [
        {"source": "generated", "schemaId": "scalar.v1", "value": {"exact": "5"}},
        {"source": "manual", "schemaId": "scalar.v1", "value": {"exact": "5"}},
        {"source": "fixture", "schemaId": "vector.v9", "value": {"exact": "5"}},
        {"source": "fixture", "schemaId": "scalar.v1", "value": {"approx": 5}},
        {"source": "generated", "schemaId": "scalar.v1", "value": {"exact": "5"}, "solverVersion": "latest"},
        {"source": "latest", "schemaId": "scalar.v1", "value": {"exact": "5"}, "solverVersion": "vectorLength.v1"},
    ],
    ids=[
        "generatedWithoutVersion",
        "manualWithoutVersion",
        "unknownSchema",
        "wrongValue",
        "mutableVersion",
        "unknownSource",
    ],
)
def testReadyOutcomeRules(outcome: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReadyOutcome.model_validate(outcome)


def testFixtureOutcomeMayHaveNoSolverVersion() -> None:
    assert ReadyOutcome(source="fixture", schemaId="scalar.v1", value={"exact": "0"}).solverVersion is None


def testPendingNeedsAPositiveWait() -> None:
    with pytest.raises(ValidationError):
        PendingOutcome(retryAfterS=0)
