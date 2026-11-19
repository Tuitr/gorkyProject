import json
from pathlib import Path
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, text

from contracts.v1 import openapi
from contracts.v1.references.reference import ReferenceRequest
from references.registry.keys import buildLookupKey

from .conftest import buildAlembicConfig

EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"


def buildRequest(**changes: Any) -> ReferenceRequest:
    body: dict[str, Any] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    body["constraints"] = {"answerForm": "exact", "bounds": {"min": 0, "max": 10}}
    return ReferenceRequest.model_validate(body | changes)


def testConstraintKeyOrderDoesNotChangeTheKey() -> None:
    reordered = buildRequest(constraints={"bounds": {"max": 10, "min": 0}, "answerForm": "exact"})

    assert buildLookupKey(reordered) == buildLookupKey(buildRequest())


def testFamilyHintDoesNotChangeTheKey() -> None:
    assert buildLookupKey(buildRequest(familyHint="vectors")) == buildLookupKey(buildRequest())


@pytest.mark.parametrize(
    "changes",
    [
        {"statement": "Длина вектора (4; 3)"},
        {"statement": "Длина  вектора (3; 4)"},
        {"constraints": {"answerForm": "decimal", "bounds": {"min": 0, "max": 10}}},
        {"constraints": {"answerForm": "exact", "point": [4, 3]}},
        {"assignmentRev": 2},
        {"assignmentId": "w2"},
        {"taskId": "t2"},
    ],
    ids=["swappedCoordinates", "extraSpace", "otherRequirement", "otherList", "nextRevision", "otherAssignment", "otherTask"],
)
def testMeaningfulChangesGiveAnotherKey(changes: dict[str, Any]) -> None:
    assert buildLookupKey(buildRequest(**changes)) != buildLookupKey(buildRequest())


def testListOrderIsPartOfTheKey() -> None:
    first = buildRequest(constraints={"point": [3, 4]})
    swapped = buildRequest(constraints={"point": [4, 3]})

    assert buildLookupKey(first) != buildLookupKey(swapped)


def testKeyIsVersioned() -> None:
    assert buildLookupKey(buildRequest()).startswith("k1:")


def testMigrationBackfillsTheSameKey(engine: Engine, databaseUrl: str) -> None:
    config = buildAlembicConfig(databaseUrl)
    request = buildRequest()
    command.downgrade(config, "0001")
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO refs.reference_records (reference_id, assignment_id, assignment_rev, task_id,"
                    " statement, constraints, state, reason_code)"
                    " VALUES ('old1', :assignmentId, :assignmentRev, :taskId, :statement, CAST(:constraints AS jsonb),"
                    " 'unsupported', 'unknown_task')"
                ),
                {
                    "assignmentId": request.assignmentId,
                    "assignmentRev": request.assignmentRev,
                    "taskId": request.taskId,
                    "statement": request.statement,
                    "constraints": json.dumps(request.constraints),
                },
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            storedKey = connection.scalar(text("SELECT lookup_key FROM refs.reference_records WHERE reference_id = 'old1'"))
    finally:
        command.upgrade(config, "head")
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM refs.reference_records WHERE reference_id = 'old1'"))

    assert storedKey == buildLookupKey(request)
