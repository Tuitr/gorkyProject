"""C7.1: lookup boundaries and terminal states of the registry, over HTTP, counting every provider call.

Earlier tests check keys one by one (testLookupKeys) and single transitions (testPendingReferences).
Here each case also counts records and provider calls: equal numbers under other constraints never share
a record, and polling a finished reference never computes it again.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from references.providers.base import ProviderOutcome, ProviderTask
from references.providers.fixture import FixtureProvider, ReferenceFixture
from references.registry.main import createApp
from references.registry.tables import ReferenceRecord
from references.settings import Settings
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"
TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["references.request"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/internal/v1/references"
DAY_S = 86400
BOUNDS = {"answerForm": "exact", "bounds": {"min": 0, "max": 10}}
BOUNDS_REORDERED = {"bounds": {"max": 10, "min": 0}, "answerForm": "exact"}


def fiveFor(fixtureId: str, statement: str, constraints: dict[str, Any]) -> ReferenceFixture:
    """A fixture whose answer is the same 5 as the packaged vectorLength34."""
    return ReferenceFixture.model_validate(
        {
            "fixtureId": fixtureId,
            "statement": statement,
            "constraints": constraints,
            "outcome": {"state": "ready", "schemaId": "scalar.v1", "value": {"exact": "5"}},
        }
    )


SAME_NUMBER_FIXTURES = [
    fiveFor("vectorLength34Decimal", "Длина вектора (3; 4)", {"answerForm": "decimal"}),
    fiveFor("vectorLength43", "Длина вектора (4; 3)", {}),
    fiveFor("vectorLength34Bounded", "Длина вектора (3; 4)", BOUNDS),
]


class CountingProvider:
    """The packaged C2 fixtures plus SAME_NUMBER_FIXTURES; remembers every start and check."""

    def __init__(self) -> None:
        self.inner = FixtureProvider(FixtureProvider.fromFile().fixtures + SAME_NUMBER_FIXTURES)
        self.starts: list[ProviderTask] = []
        self.checks: list[ProviderTask] = []

    def start(self, task: ProviderTask) -> ProviderOutcome:
        self.starts.append(task)
        return self.inner.start(task)

    def check(self, task: ProviderTask) -> ProviderOutcome:
        self.checks.append(task)
        return self.inner.check(task)


def buildClient(sessionFactory: sessionmaker[Session], provider: CountingProvider, clock: ManualClock) -> TestClient:
    settings = Settings(databaseUrl="unused", serviceTokensJson=TOKENS)
    return TestClient(createApp(settings, sessionFactory=sessionFactory, provider=provider, clock=clock))


def requestBody(**changes: Any) -> dict[str, Any]:
    body: dict[str, Any] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    return body | changes


def countRecords(sessionFactory: sessionmaker[Session]) -> int:
    with sessionFactory() as session:
        return session.scalar(select(func.count()).select_from(ReferenceRecord)) or 0


PAIRS: dict[str, tuple[dict[str, Any], dict[str, Any], bool]] = {
    "sameRequest": ({}, {}, True),
    "constraintKeyOrder": ({"constraints": BOUNDS}, {"constraints": BOUNDS_REORDERED}, True),
    "familyHintOnly": ({}, {"familyHint": "vectorLength"}, True),
    "otherConstraintsSameNumber": ({}, {"constraints": {"answerForm": "decimal"}}, False),
    "swappedCoordinatesSameNumber": ({}, {"statement": "Длина вектора (4; 3)"}, False),
    "otherTask": ({}, {"taskId": "t2"}, False),
    "otherAssignment": ({}, {"assignmentId": "w2"}, False),
    "nextRevision": ({}, {"assignmentRev": 2}, False),
}


@pytest.mark.parametrize("name", list(PAIRS))
def testLookupBoundaries(sessionFactory: sessionmaker[Session], name: str) -> None:
    firstChanges, secondChanges, shared = PAIRS[name]
    provider = CountingProvider()
    client = buildClient(sessionFactory, provider, ManualClock())

    first = client.post(URL, json=requestBody(**firstChanges), headers=AUTH).json()
    second = client.post(URL, json=requestBody(**secondChanges), headers=AUTH).json()

    for result in (first, second):
        assert (result["state"], result["schemaId"], result["value"]) == ("ready", "scalar.v1", {"exact": "5"})
    assert (first["referenceId"] == second["referenceId"]) is shared
    assert countRecords(sessionFactory) == (1 if shared else 2)
    assert len(provider.starts) == (1 if shared else 2)


TERMINAL = {
    "ready": ("Длина вектора (3; 4)", 0, "ready", None),
    "unknownTask": ("Длина вектора (2; 9)", 0, "unsupported", "unknown_task"),
    "pendingThenReady": ("Длина вектора (6; 8)", 10, "ready", None),
    "pendingThenValidationFailed": (
        "Длина вектора (5; 12)", 10, "validation_failed", "independent_tests_failed"
    ),
}


@pytest.mark.parametrize("name", list(TERMINAL))
def testFinishedReferenceIsNeverComputedAgain(sessionFactory: sessionmaker[Session], name: str) -> None:
    statement, waitS, state, reasonCode = TERMINAL[name]
    provider = CountingProvider()
    clock = ManualClock()
    client = buildClient(sessionFactory, provider, clock)

    posted = client.post(URL, json=requestBody(statement=statement), headers=AUTH)
    referenceId = posted.json()["referenceId"]
    clock.advance(waitS)
    final = client.get(f"{URL}/{referenceId}", headers=AUTH)
    repeats = []
    for _ in range(3):
        clock.advance(DAY_S)
        repeats.append(client.get(f"{URL}/{referenceId}", headers=AUTH))
        repeats.append(client.post(URL, json=requestBody(statement=statement), headers=AUTH))

    assert posted.status_code == (202 if waitS else 200)
    assert final.status_code == 200
    assert final.json()["referenceId"] == referenceId
    assert final.json()["state"] == state
    assert final.json().get("reasonCode") == reasonCode
    assert ("schemaId" in final.json()) is (state == "ready")
    assert [(repeat.status_code, repeat.json()) for repeat in repeats] == [(200, final.json())] * 6
    assert countRecords(sessionFactory) == 1
    assert len(provider.starts) == 1
    assert len(provider.checks) == (1 if waitS else 0)


def testPendingIsCheckedOnceAndOnlyAfterThePromisedWait(sessionFactory: sessionmaker[Session]) -> None:
    provider = CountingProvider()
    clock = ManualClock()
    client = buildClient(sessionFactory, provider, clock)
    body = requestBody(statement="Длина вектора (6; 8)")

    referenceId = client.post(URL, json=body, headers=AUTH).json()["referenceId"]
    early = []
    for _ in range(3):
        clock.advance(3)
        early.append(client.get(f"{URL}/{referenceId}", headers=AUTH).json())
        early.append(client.post(URL, json=body, headers=AUTH).json())
    checksBeforeTheWait = len(provider.checks)
    clock.advance(1)
    ready = [client.get(f"{URL}/{referenceId}", headers=AUTH).json() for _ in range(3)]

    assert [result["retryAfterS"] for result in early] == [7, 7, 4, 4, 1, 1]
    assert {result["referenceId"] for result in early + ready} == {referenceId}
    assert checksBeforeTheWait == 0
    assert ready[0]["state"] == "ready"
    assert ready == [ready[0]] * 3
    assert len(provider.starts) == 1
    assert len(provider.checks) == 1
    assert countRecords(sessionFactory) == 1
