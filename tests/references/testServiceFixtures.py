"""C5.2: the reference and review answers handed to developer 2, replayed through the real internal routes.

The exchanges in contracts/v1/examples/references/exchanges and contracts/v1/examples/gateway/exchanges are the
same files developer 2's client reads in tests/grading/testServiceExamples.py. Here they go through the registry
and the gateway with the fixture provider and the fixture transport, so a change on either side breaks a test.
The open-set fixtures must agree with the hand-checked keys (C5.1) and with the manifest of the mini set, so no
answer of one task can stand in for another; the closed split never appears in a transport file.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

import references
from contracts.v1 import openapi
from contracts.v1.references.modelreview import ModelReviewResponse
from contracts.v1.references.reference import ReferenceReady, ReferenceRequest, referenceResultAdapter
from dataset.generate import DEFAULT_SEED, generateDataset
from references.gateway.main import createApp as createGateway
from references.gateway.settings import GatewaySettings
from references.registry.main import createApp as createRegistry
from references.settings import Settings
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples"
REFERENCE_EXCHANGES = EXAMPLES / "references" / "exchanges"
REVIEW_EXCHANGES = EXAMPLES / "gateway" / "exchanges"
FIXTURES = Path(references.__file__).parent / "fixtures"
TRANSPORT_FILES = ("references.json", "reviews.json", "reviewFailures.json")
OPEN_SPLITS = {"demo", "e2e"}
TOKEN = "grading-test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

JsonObject = dict[str, Any]


def readJson(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def names(directory: Path) -> list[str]:
    return sorted(path.name for path in directory.glob("*.json"))


def tokens(right: str) -> str:
    return json.dumps({TOKEN: {"serviceName": "grading", "rights": [right]}})


@pytest.fixture(scope="module")
def manifest() -> JsonObject:
    document: JsonObject = generateDataset(DEFAULT_SEED).manifest.model_dump(mode="json")
    return document


def openCases(manifest: JsonObject) -> dict[str, JsonObject]:
    return {case["caseId"]: case for case in manifest["cases"] if case["split"] in OPEN_SPLITS}


def withReferenceId(body: JsonObject, referenceId: str) -> JsonObject:
    return body | {"referenceId": referenceId}


@pytest.mark.parametrize("name", names(REFERENCE_EXCHANGES))
def testReferenceExchangeReplaysOnTheRegistry(sessionFactory: sessionmaker[Session], name: str) -> None:
    exchange = readJson(REFERENCE_EXCHANGES / name)
    clock = ManualClock()
    settings = Settings(databaseUrl="unused", serviceTokensJson=tokens("references.request"))
    client = TestClient(createRegistry(settings, sessionFactory=sessionFactory, clock=clock))
    ReferenceRequest.model_validate(exchange["request"])
    referenceId = ""

    for step in exchange["steps"]:
        clock.advance(step["waitS"])
        if step["method"] == "POST":
            response = client.post(step["path"], json=exchange["request"], headers=AUTH)
            referenceId = response.json()["referenceId"]
        else:
            response = client.get(step["path"].format(referenceId=referenceId), headers=AUTH)
        expected = referenceResultAdapter.validate_python(step["body"])

        assert response.status_code == step["status"], (name, step["method"])
        assert response.json() == withReferenceId(step["body"], referenceId), (name, step["method"])
        assert response.json()["referenceId"] == referenceId, "one referenceId from POST to the final GET"
        if isinstance(expected, ReferenceReady):
            assert (expected.source, expected.solverVersion) == ("fixture", None)


@pytest.mark.parametrize("name", names(REVIEW_EXCHANGES))
def testReviewExchangeReplaysOnTheGateway(sessionFactory: sessionmaker[Session], name: str) -> None:
    exchange = readJson(REVIEW_EXCHANGES / name)
    settings = GatewaySettings(transport="fixture", serviceTokensJson=tokens("model.review"))
    client = TestClient(createGateway(settings, sessionFactory=sessionFactory, clock=ManualClock()))

    response = client.post("/internal/v1/model/review", json=exchange["request"], headers=AUTH)

    assert response.status_code == exchange["status"]
    assert response.json() == exchange["response"]
    assert ModelReviewResponse.model_validate(response.json()).taskResult.taskId == exchange["request"]["taskId"]


def testReadyFixturesAreFixtureSourcedWithoutSolverVersion() -> None:
    ready = [
        referenceResultAdapter.validate_python(step["body"])
        for name in names(REFERENCE_EXCHANGES)
        for step in readJson(REFERENCE_EXCHANGES / name)["steps"]
        if step["body"]["state"] == "ready"
    ]

    assert ready and all(isinstance(result, ReferenceReady) for result in ready)
    assert {(result.source, result.solverVersion) for result in ready if isinstance(result, ReferenceReady)} == {
        ("fixture", None)
    }


def testOpenSetReferenceFixturesAreTheVerifiedKeys() -> None:
    keys = {task["statement"]: task["expectedReference"] for task in readJson(FIXTURES / "openSetKeys.json")["tasks"]}
    fixtures = {fixture["statement"]: fixture for fixture in readJson(FIXTURES / "references.json")}

    assert set(keys) <= set(fixtures), "every open-set task has a reference fixture"
    for statement, key in keys.items():
        outcome = fixtures[statement]["outcome"]
        assert (outcome["state"], outcome["schemaId"], outcome["value"]) == ("ready", key["schemaId"], key["value"])
        assert fixtures[statement].get("pendingForS", 0) == 0


def testOpenSetReviewFixturesAgreeWithTheManifest(manifest: JsonObject) -> None:
    cases = openCases(manifest)
    fixtures = {fixture["fixtureId"]: fixture for fixture in readJson(FIXTURES / "reviews.json")}
    reviewed = {caseId for caseId, case in cases.items() if case["expectedVerdict"]["answer"] != "skipped"}

    assert reviewed <= set(fixtures), "every reviewed open case has a recorded reply"
    for caseId in reviewed:
        case, fixture = cases[caseId], fixtures[caseId]
        content = fixture["content"]
        verdict = {field: content[field] for field in ("answer", "reasoning", "method", "needsReview")}
        issues = [
            {
                "issueId": issue["issueId"],
                "stepId": issue["stepId"],
                "category": issue["category"],
                "causedByIssueId": issue.get("causedByIssueId"),
            }
            for issue in content["issues"]
        ]

        assert fixture["taskId"] == content["taskId"] == f"t{case['assignmentRef']['taskNumber']}", caseId
        assert fixture["statement"] == case["assignmentRef"]["statement"], caseId
        assert fixture["steps"] == case["expectedSteps"], caseId
        assert verdict == case["expectedVerdict"], caseId
        assert issues == case["expectedIssues"], caseId


def testReviewExchangesAreRecordedOpenCases(manifest: JsonObject) -> None:
    cases = openCases(manifest)
    for name in names(REVIEW_EXCHANGES):
        exchange = readJson(REVIEW_EXCHANGES / name)
        case = cases[exchange["caseId"]]

        assert exchange["request"]["statement"] == case["assignmentRef"]["statement"], name
        assert exchange["request"]["steps"] == case["expectedSteps"], name
        assert exchange["request"]["reference"]["value"] == case["expectedReference"]["value"], name


def testClosedSplitIsAbsentFromTransportFilesAndExamples(manifest: JsonObject) -> None:
    closed = [case for case in manifest["cases"] if case["split"] == "closed"]
    secrets = {case["assignmentRef"]["statement"] for case in closed} | {
        step["markdown"] for case in closed for step in case["expectedSteps"] if "Ответ" not in step["markdown"]
    }
    texts = [(FIXTURES / name).read_text(encoding="utf-8") for name in TRANSPORT_FILES]
    examples = (*REFERENCE_EXCHANGES.glob("*.json"), *REVIEW_EXCHANGES.glob("*.json"))
    texts += [path.read_text(encoding="utf-8") for path in examples]
    encoded = [json.dumps(secret, ensure_ascii=False)[1:-1] for secret in secrets]

    assert secrets
    assert not any(secret in text for secret in encoded for text in texts)
