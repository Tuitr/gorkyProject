"""C6.3: the state table of docs/generation.md matches the v1 contract and what the API answers today."""

import json
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi.testclient import TestClient
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

import references
from contracts.v1 import openapi
from contracts.v1.references.reference import (
    ReferencePending,
    ReferenceReady,
    ReferenceState,
    ReferenceUnsupported,
    ReferenceValidationFailed,
)
from references.providers.fixture import UNKNOWN_TASK, FixtureProvider
from references.registry.main import createApp
from references.settings import Settings
from shared.clock import ManualClock

DOC = Path(references.__file__).parents[1] / "docs" / "generation.md"
EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"
TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["references.request"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
STATE_MODELS: list[type[BaseModel]] = [
    ReferenceReady,
    ReferencePending,
    ReferenceUnsupported,
    ReferenceValidationFailed,
]

CURRENT_EXAMPLES = {
    ("ready", None): "Длина вектора (3; 4)",
    ("pending", None): "Длина вектора (6; 8)",
    ("unsupported", UNKNOWN_TASK): "Длина вектора (2; 9)",
    ("validation_failed", "independent_tests_failed"): "Длина вектора (5; 12)",
}
"""One request per row marked «да»: a new current row in the doc needs an example here."""


def readStateTable() -> list[dict[str, str]]:
    lines = DOC.read_text(encoding="utf-8").split("## Состояния и причины", 1)[1].splitlines()
    rows = [line for line in lines if line.startswith("|")]
    header = cells(rows[0])
    return [dict(zip(header, cells(row), strict=True)) for row in rows[2:]]


def cells(row: str) -> list[str]:
    return [cell.strip().strip("`") for cell in row.strip().strip("|").split("|")]


def reasonOf(row: dict[str, str]) -> str | None:
    return None if row["reasonCode"] == "—" else row["reasonCode"]


def stateOf(model: type[BaseModel]) -> str:
    (state,) = get_args(model.model_fields["state"].annotation)
    return str(state)


def documentedStatuses() -> dict[str, int]:
    """state -> HTTP status of POST /internal/v1/references in the committed OpenAPI."""
    responses = openapi.buildReferencesOpenApi()["paths"]["/internal/v1/references"]["post"]["responses"]
    byName = {model.__name__: stateOf(model) for model in STATE_MODELS}
    statuses: dict[str, int] = {}
    for status, response in responses.items():
        schema: dict[str, Any] = response["content"]["application/json"]["schema"]
        for ref in schema.get("oneOf", [schema]):
            name = ref["$ref"].rsplit("/", 1)[1]
            if name in byName:
                statuses[byName[name]] = int(status)
    return statuses


def testTableNamesEveryContractStateWithItsStatus() -> None:
    rows = readStateTable()

    assert {row["state"] for row in rows} == set(get_args(ReferenceState))
    statuses = documentedStatuses()
    for row in rows:
        assert int(row["HTTP"]) == statuses[row["state"]], row


def testOnlyRefusalsHaveReasons() -> None:
    for row in readStateTable():
        refusal = row["state"] in ("unsupported", "validation_failed")
        assert (reasonOf(row) is not None) == refusal, row


def testCurrentRowsAreExactlyWhatFixturesProduce() -> None:
    current = {(row["state"], reasonOf(row)) for row in readStateTable() if row["Сейчас"] == "да"}
    fixtureReasons = {
        ("validation_failed", fixture.outcome.reasonCode)
        for fixture in FixtureProvider.fromFile().fixtures
        if fixture.outcome.reasonCode is not None
    }

    assert current == set(CURRENT_EXAMPLES)
    assert fixtureReasons | {("unsupported", UNKNOWN_TASK)} <= current


@pytest.mark.parametrize(("state", "reasonCode"), sorted(CURRENT_EXAMPLES, key=str))
def testCurrentRowsMatchTheRunningApi(
    sessionFactory: sessionmaker[Session], state: str, reasonCode: str | None
) -> None:
    (row,) = [row for row in readStateTable() if (row["state"], reasonOf(row)) == (state, reasonCode)]
    clock = ManualClock()
    client = TestClient(
        createApp(Settings(databaseUrl="unused", serviceTokensJson=TOKENS), sessionFactory=sessionFactory, clock=clock)
    )
    body = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))

    response = client.post(
        "/internal/v1/references", json=body | {"statement": CURRENT_EXAMPLES[(state, reasonCode)]}, headers=AUTH
    )
    if state != "pending" and response.json()["state"] == "pending":
        clock.advance(response.json()["retryAfterS"])
        response = client.get(f"/internal/v1/references/{response.json()['referenceId']}", headers=AUTH)

    assert response.status_code == int(row["HTTP"])
    assert response.json()["state"] == state
    assert response.json().get("reasonCode") == reasonCode
