import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from references.registry.main import createApp
from references.registry.tables import ReferenceRecord
from references.settings import Settings
from shared.clock import ManualClock

TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["references.request"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"


def buildClient(sessionFactory: sessionmaker[Session], clock: ManualClock) -> TestClient:
    settings = Settings(databaseUrl="unused", serviceTokensJson=TOKENS)
    return TestClient(createApp(settings, sessionFactory=sessionFactory, clock=clock))


def requestBody(statement: str) -> dict[str, object]:
    body: dict[str, object] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    return body | {"statement": statement}


def countRecords(sessionFactory: sessionmaker[Session]) -> int:
    with sessionFactory() as session:
        return session.scalar(select(func.count()).select_from(ReferenceRecord)) or 0


def testPendingThenReadyKeepsReferenceId(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    client = buildClient(sessionFactory, clock)

    posted = client.post("/internal/v1/references", json=requestBody("Длина вектора (6; 8)"), headers=AUTH)
    referenceId = posted.json()["referenceId"]
    clock.advance(4)
    early = client.get(f"/internal/v1/references/{referenceId}", headers=AUTH)
    clock.advance(6)
    ready = client.get(f"/internal/v1/references/{referenceId}", headers=AUTH)
    again = client.get(f"/internal/v1/references/{referenceId}", headers=AUTH)

    assert posted.status_code == 202
    assert posted.json() == {"referenceId": referenceId, "state": "pending", "retryAfterS": 10}
    assert early.json() == {"referenceId": referenceId, "state": "pending", "retryAfterS": 6}
    assert ready.status_code == 200
    assert ready.json()["state"] == "ready"
    assert ready.json()["referenceId"] == referenceId
    assert ready.json()["value"] == {"exact": "10"}
    assert again.json() == ready.json()
    assert countRecords(sessionFactory) == 1


def testPendingThenValidationFailedHasReason(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    client = buildClient(sessionFactory, clock)

    referenceId = client.post(
        "/internal/v1/references", json=requestBody("Длина вектора (5; 12)"), headers=AUTH
    ).json()["referenceId"]
    clock.advance(10)
    final = client.get(f"/internal/v1/references/{referenceId}", headers=AUTH).json()

    assert final == {"referenceId": referenceId, "state": "validation_failed", "reasonCode": "independent_tests_failed"}


def testRepeatedPostWhilePendingReturnsSameRecord(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    client = buildClient(sessionFactory, clock)

    first = client.post("/internal/v1/references", json=requestBody("Длина вектора (6; 8)"), headers=AUTH).json()
    second = client.post("/internal/v1/references", json=requestBody("Длина вектора (6; 8)"), headers=AUTH).json()

    assert second["referenceId"] == first["referenceId"]
    assert countRecords(sessionFactory) == 1


def testUnknownIdIs404AndCreatesNothing(sessionFactory: sessionmaker[Session]) -> None:
    response = buildClient(sessionFactory, ManualClock()).get("/internal/v1/references/ref_missing", headers=AUTH)

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"
    assert countRecords(sessionFactory) == 0
