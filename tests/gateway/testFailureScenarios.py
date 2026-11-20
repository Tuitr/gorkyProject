"""C5.3: scripted provider failures for debugging, on in test configuration only, the same on every run."""

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from contracts.v1.common.errors import ApiError
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from references.gateway.main import createApp
from references.gateway.settings import GatewaySettings, buildTransport
from references.gateway.transports.base import ConfigError
from references.gateway.transports.fixture import (
    DEFAULT_REVIEWS_PATH,
    FAILURE_SCENARIOS_PATH,
    FixtureTransport,
    ReviewFixture,
)
from references.settings import DB_SCHEMA
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"
SCENARIOS = sorted(path.stem for path in (EXAMPLES / "failures").glob("*.json"))
TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["model.review"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}
URL = "/internal/v1/model/review"


def readScenario(name: str) -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads((EXAMPLES / "failures" / f"{name}.json").read_text(encoding="utf-8"))
    return loaded


def settingsFromScenario(scenario: dict[str, Any]) -> GatewaySettings:
    settings = GatewaySettings.fromEnv(scenario["gatewayEnv"] | {"GATEWAY_SERVICE_TOKENS": TOKENS})
    assert settings.failureScenarios
    return settings


def runScenario(sessionFactory: sessionmaker[Session], scenario: dict[str, Any]) -> dict[str, Any]:
    """Send the example request as many times as it lists responses; record what developer 2 would see."""
    settings = settingsFromScenario(scenario)
    clock = ManualClock()
    transport = buildTransport(settings)
    assert isinstance(transport, FixtureTransport)
    client = TestClient(createApp(settings, sessionFactory=sessionFactory, transport=transport, clock=clock))
    headers = AUTH | scenario["request"]["headers"]
    responses = []
    for _ in scenario["responses"]:
        response = client.post(URL, json=scenario["request"]["body"], headers=headers)
        responses.append(
            {"status": response.status_code, "retryAfter": response.headers.get("Retry-After"), "body": response.json()}
        )
    return {"responses": responses, "providerCalls": len(transport.calls), "gatewaySleepsS": clock.sleeps}


def emptyGatewayTables(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(text(f'TRUNCATE "{DB_SCHEMA}".review_calls, "{DB_SCHEMA}".provider_slots'))


@pytest.mark.parametrize("name", SCENARIOS)
def testScenarioGivesTheDocumentedAnswers(sessionFactory: sessionmaker[Session], name: str) -> None:
    scenario = readScenario(name)

    observed = runScenario(sessionFactory, scenario)

    assert observed == {key: scenario[key] for key in ("responses", "providerCalls", "gatewaySleepsS")}


@pytest.mark.parametrize("name", SCENARIOS)
def testTwoRunsGiveTheSameSequence(engine: Engine, sessionFactory: sessionmaker[Session], name: str) -> None:
    scenario = readScenario(name)

    first = runScenario(sessionFactory, scenario)
    emptyGatewayTables(engine)
    second = runScenario(sessionFactory, scenario)

    assert first == second


def testEachRequestIdGetsItsOwnSequence(sessionFactory: sessionmaker[Session]) -> None:
    """Another student's request does not inherit the calls already spent on the first one."""
    scenario = readScenario("rateLimitedThenOk")
    settings = settingsFromScenario(scenario)
    clock = ManualClock()
    transport = buildTransport(settings)
    assert isinstance(transport, FixtureTransport)
    client = TestClient(createApp(settings, sessionFactory=sessionFactory, transport=transport, clock=clock))
    body = scenario["request"]["body"]

    first = client.post(URL, json=body, headers=AUTH)
    second = client.post(URL, json=body | {"requestId": "q-second"}, headers=AUTH)

    assert first.status_code == second.status_code == 200
    assert len(transport.calls) == 4
    assert clock.sleeps == [2.0, 2.0]


@pytest.mark.parametrize("name", SCENARIOS)
def testScenarioExamplesFollowTheContract(name: str) -> None:
    scenario = readScenario(name)

    ModelReviewRequest.model_validate(scenario["request"]["body"])
    for response in scenario["responses"]:
        model = ModelReviewResponse if response["status"] == 200 else ApiError
        model.model_validate(response["body"])
        if response["status"] != 200:
            assert response["body"]["traceId"] == scenario["request"]["headers"]["X-Trace-Id"]


def testEveryFailureFixtureHasAnExample() -> None:
    fixtures = json.loads(FAILURE_SCENARIOS_PATH.read_text(encoding="utf-8"))
    tasks = {readScenario(name)["request"]["body"]["taskId"] for name in SCENARIOS}

    assert {fixture["taskId"] for fixture in fixtures} == tasks


def testScenariosAreOffInTheUsualConfiguration(sessionFactory: sessionmaker[Session]) -> None:
    scenario = readScenario("rateLimitedAlways")
    settings = GatewaySettings.fromEnv({"GATEWAY_TRANSPORT": "fixture", "GATEWAY_SERVICE_TOKENS": TOKENS})
    transport = buildTransport(settings)
    client = TestClient(createApp(settings, sessionFactory=sessionFactory, transport=transport, clock=ManualClock()))

    response = client.post(URL, json=scenario["request"]["body"], headers=AUTH)

    assert not settings.failureScenarios
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "Retry-After" not in response.headers


def testRequestCannotChooseAScenario(sessionFactory: sessionmaker[Session]) -> None:
    """With scenarios on, a normal request stays normal whatever query or header it carries."""
    settings = settingsFromScenario(readScenario("timeoutAlways"))
    transport = buildTransport(settings)
    assert isinstance(transport, FixtureTransport)
    client = TestClient(createApp(settings, sessionFactory=sessionFactory, transport=transport, clock=ManualClock()))
    body = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    sneaky = {"X-Fixture-Scenario": "timeoutAlways", "X-Failure": "timeout"}

    response = client.post(f"{URL}?scenario=timeoutAlways&failure=timeout", json=body, headers=AUTH | sneaky)
    withField = client.post(URL, json=body | {"requestId": "q2", "scenario": "timeoutAlways"}, headers=AUTH)

    assert response.status_code == 200
    assert response.json() == json.loads((EXAMPLES / "responseAnswerOnly.json").read_text(encoding="utf-8"))
    assert withField.status_code == 422
    assert len(transport.calls) == 1


@pytest.mark.parametrize(
    "environ",
    [
        {"GATEWAY_TRANSPORT": "gigachat", "GIGACHAT_AUTH_KEY": "key", "GATEWAY_FAILURE_SCENARIOS": "1"},
        {"GATEWAY_TRANSPORT": "fixture", "GATEWAY_FAILURE_SCENARIOS": "yes"},
        {"GATEWAY_TRANSPORT": "fixture", "GATEWAY_FAILURE_SCENARIOS": "true"},
    ],
    ids=["withGigachat", "yes", "true"],
)
def testOnlyAnExplicitTestSwitchTurnsScenariosOn(environ: dict[str, str]) -> None:
    with pytest.raises(ConfigError):
        GatewaySettings.fromEnv(environ)


def testLiveTransportCannotTakeScenarios() -> None:
    with pytest.raises(ConfigError, match="GATEWAY_TRANSPORT=fixture"):
        GatewaySettings(transport="gigachat", failureScenarios=True)


def testFixtureNeedsAReplyOrAFailure() -> None:
    with pytest.raises(ValidationError):
        ReviewFixture.model_validate(
            {"fixtureId": "f", "taskId": "t", "statement": "s", "steps": [{"stepId": "s1", "markdown": "x = 1"}]}
        )


def testFailureFixturesLoadBesideTheReviews() -> None:
    transport = FixtureTransport.fromFile(DEFAULT_REVIEWS_PATH, FAILURE_SCENARIOS_PATH)

    assert {fixture.fixtureId for fixture in transport.fixtures} >= {"vectorLength34AnswerOnly", "failInvalidJson"}
