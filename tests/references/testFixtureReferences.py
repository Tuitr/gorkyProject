import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from contracts.v1.references.reference import ReferenceRequest, referenceResultAdapter
from references.providers.fixture import FixtureProvider, ReferenceFixture
from references.registry.main import createApp
from references.settings import Settings

TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["references.request"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"


def buildClient(sessionFactory: sessionmaker[Session]) -> TestClient:
    settings = Settings(databaseUrl="unused", serviceTokensJson=TOKENS)
    return TestClient(createApp(settings, sessionFactory=sessionFactory))


def requestBody(**changes: object) -> dict[str, object]:
    body: dict[str, object] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    return body | changes


def testKnownFixtureReturnsReady(sessionFactory: sessionmaker[Session]) -> None:
    response = buildClient(sessionFactory).post("/internal/v1/references", json=requestBody(), headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["referenceId"].startswith("ref_")
    assert {key: body[key] for key in ("state", "source", "schemaId", "value", "solverVersion")} == {
        "state": "ready",
        "source": "fixture",
        "schemaId": "scalar.v1",
        "value": {"exact": "5"},
        "solverVersion": None,
    }
    referenceResultAdapter.validate_python(body)


def testChangedStatementIsUnsupported(sessionFactory: sessionmaker[Session]) -> None:
    response = buildClient(sessionFactory).post(
        "/internal/v1/references", json=requestBody(statement="Длина вектора (3; 5)"), headers=AUTH
    )

    assert response.status_code == 200
    assert response.json()["state"] == "unsupported"
    assert response.json()["reasonCode"] == "unknown_task"


def testChangedConstraintsAreUnsupported(sessionFactory: sessionmaker[Session]) -> None:
    response = buildClient(sessionFactory).post(
        "/internal/v1/references", json=requestBody(constraints={"answerForm": "decimal"}), headers=AUTH
    )

    assert response.json()["state"] == "unsupported"


def testSameRequestReusesReferenceId(sessionFactory: sessionmaker[Session]) -> None:
    client = buildClient(sessionFactory)

    first = client.post("/internal/v1/references", json=requestBody(), headers=AUTH).json()
    second = client.post("/internal/v1/references", json=requestBody(), headers=AUTH).json()
    otherRevision = client.post("/internal/v1/references", json=requestBody(assignmentRev=2), headers=AUTH).json()

    assert first["referenceId"] == second["referenceId"]
    assert otherRevision["referenceId"] != first["referenceId"]


def testRequestWithoutTokenIsRejected(sessionFactory: sessionmaker[Session]) -> None:
    response = buildClient(sessionFactory).post("/internal/v1/references", json=requestBody())

    assert response.status_code == 401
    assert response.json()["code"] == "unauthorized"


def testFixtureMatchIgnoresConstraintKeyOrder() -> None:
    provider = FixtureProvider(
        [
            ReferenceFixture.model_validate(
                {
                    "fixtureId": "f1",
                    "statement": "Длина вектора (3; 4)",
                    "constraints": {"a": 1, "b": 2},
                    "outcome": {"state": "ready", "schemaId": "scalar.v1", "value": {"exact": "5"}},
                }
            )
        ]
    )
    request = ReferenceRequest.model_validate(requestBody(constraints={"b": 2, "a": 1}))

    fixture = provider.find(request)

    assert fixture is not None and fixture.fixtureId == "f1"


@pytest.mark.parametrize(
    "outcome",
    [
        {"state": "ready", "schemaId": "scalar.v1"},
        {"state": "ready", "schemaId": "vector.v9", "value": {"exact": "5"}},
        {"state": "ready", "schemaId": "scalar.v1", "value": {"approx": 5}},
        {"state": "validation_failed"},
    ],
    ids=["readyWithoutValue", "unknownSchema", "wrongValueShape", "failureWithoutReason"],
)
def testInvalidFixtureIsRejectedAtLoad(outcome: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        ReferenceFixture.model_validate(
            {"fixtureId": "bad", "statement": "x", "constraints": {}, "outcome": outcome}
        )


def testPackagedFixturesLoad() -> None:
    provider = FixtureProvider.fromFile()

    assert {fixture.fixtureId for fixture in provider.fixtures} >= {"vectorLength34", "vectorLengthZero"}
