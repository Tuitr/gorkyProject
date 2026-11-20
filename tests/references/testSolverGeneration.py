"""C6.3: solvers generated per task, admitted only when independent solvers agree (decided 2026-11-20).

Everything runs on the fixture transport (fixtures/solverAuthors.json or fixtures built here) and the local
sandbox: no network and no paid model. The stand runs the same code with solver-builder and solver-sandbox.
"""

import json
import threading
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.references.solverartifact import SolverArtifact, metadataKey
from references.gateway.transports.base import ModelCall, ModelReply, TransportError
from references.gateway.transports.fixture import AuthorFixture, FixtureTransport
from references.providers.base import PendingOutcome, ProviderTask, ReadyOutcome, RefusedOutcome
from references.providers.fixture import FixtureProvider
from references.providers.generating import GeneratingProvider
from references.registry.main import createApp
from references.settings import Settings, SettingsError
from references.solvers.answers import answersAgree
from references.solvers.artifacts import MemoryArtifactStore
from references.solvers.author import AuthorLog, AuthorTask, SolverAuthor, readCode
from references.solvers.builder import SolverBuilder, sandboxFromEnv
from references.solvers.policy import CodeRefused, checkSolverCode
from references.solvers.sandbox import DirectorySandbox, LocalSandbox
from references.solvers.sandboxd import serve
from references.solvers.tables import SolverBuildRecord
from shared.clock import ManualClock

QUADRATIC = "Решите уравнение x² − 5x + 6 = 0."
NEW_TASK = "Найдите сумму чисел 17 и 25."
TOKEN = "grading-test-token"
TOKENS = json.dumps({TOKEN: {"serviceName": "grading", "rights": ["references.request"]}})
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def code(answer: str) -> dict[str, object]:
    return {"code": f"def solve(task):\n    return {{'exact': {answer!r}}}\n"}


def author(fixtureId: str, approach: str, content: object, *, statement: str = NEW_TASK, retry: bool = False) -> AuthorFixture:
    return AuthorFixture.model_validate(
        {"fixtureId": fixtureId, "statement": statement, "approach": approach, "retry": retry, "content": content}
    )


def task(statement: str = NEW_TASK) -> ProviderTask:
    return ProviderTask(assignmentRev=1, statement=statement, constraints={})


class Stand:
    def __init__(self, sessionFactory: sessionmaker[Session], transport: FixtureTransport | None = None) -> None:
        self.clock = ManualClock()
        self.transport = transport if transport is not None else FixtureTransport.fromFile()
        self.artifacts = MemoryArtifactStore()
        self.sessionFactory = sessionFactory
        self.provider = GeneratingProvider(FixtureProvider.fromFile(), sessionFactory, self.clock, buildDeadlineS=600)
        self.author = SolverAuthor(self.transport, LocalSandbox(), clock=self.clock)
        self.builder = SolverBuilder(self.provider.store, self.author, self.artifacts, clock=self.clock)

    def build(self, providerTask: ProviderTask) -> SolverBuildRecord:
        assert isinstance(self.provider.start(providerTask), PendingOutcome)
        assert self.builder.runOnce()
        with self.sessionFactory() as session:
            return session.scalars(select(SolverBuildRecord)).one()


def testAnswersAgreeAsNumbersAndAsMultisets() -> None:
    assert answersAgree("2; 3", "3;2")
    assert answersAgree("-2; 1/2", "0.5; −2")
    assert answersAgree("2,5", "5/2")
    assert answersAgree("нет решений", "Нет  решений")
    assert not answersAgree("2, 3", "2; 3")
    assert not answersAgree("1/3", "0.333")
    assert not answersAgree("3", "3; 3")


def testPolicyRefusesAnythingButArithmetic() -> None:
    checkSolverCode("from fractions import Fraction\n\ndef solve(task):\n    return {'exact': str(Fraction(1, 2))}\n")
    for bad in (
        "import os\ndef solve(task):\n    return {}",
        "def solve(task):\n    return open('/etc/passwd').read()",
        "def solve(task):\n    return ().__class__.__base__.__subclasses__()",
        "def solve(task):\n    return eval('1')",
        "def helper():\n    return 1",
        "def solve(task) return",
    ):
        with pytest.raises(CodeRefused):
            checkSolverCode(bad)


def testReplyIsReadFromJsonOrAFence() -> None:
    body = "def solve(task):\n    return {'exact': '1'}\n"
    assert readCode(json.dumps({"code": body})) == body
    assert readCode("```json\n" + json.dumps({"code": body}) + "\n```") == body
    assert readCode("Вот код:\n```python\n" + body + "```") == body.strip()
    with pytest.raises(CodeRefused):
        readCode("Ответ: 42")


def testSandboxStopsMisbehavingCode() -> None:
    sandbox = LocalSandbox(timeoutS=2)

    assert sandbox.run(code("42")["code"], {}).value == {"exact": "42"}  # type: ignore[arg-type]
    assert "longer than" in (sandbox.run("def solve(task):\n    while True:\n        pass\n", {}).error or "")
    assert "ImportError" in (sandbox.run("def solve(task):\n    import socket\n", {}).error or "")
    assert "NameError" in (sandbox.run("def solve(task):\n    return open('/etc/hostname')\n", {}).error or "")
    assert "must return a dict" in (sandbox.run("def solve(task):\n    return 5\n", {}).error or "")


def testDirectorySandboxTalksToTheDaemon(tmp_path: Path) -> None:
    stopped = threading.Event()
    daemon = threading.Thread(target=serve, args=(tmp_path,), kwargs={"uid": None, "pollS": 0.05, "stop": stopped.is_set})
    daemon.start()
    try:
        sandbox = DirectorySandbox(tmp_path, timeoutS=5, waitS=20, pollS=0.05)
        result = sandbox.run(code("7")["code"], {"statement": "s", "constraints": {}})  # type: ignore[arg-type]
    finally:
        stopped.set()
        daemon.join()

    assert result.ok and result.value == {"exact": "7"}
    assert list(tmp_path.iterdir()) == []


def testQuadraticTaskGetsAGeneratedReference(sessionFactory: sessionmaker[Session]) -> None:
    stand = Stand(sessionFactory)

    build = stand.build(task(QUADRATIC))
    ready = stand.provider.check(task(QUADRATIC))

    assert build.state == "published" and build.modelCalls == 2
    assert isinstance(ready, ReadyOutcome)
    assert ready.source == "generated" and ready.value == {"exact": "2; 3"}
    assert ready.solverVersion is not None and ready.solverVersion == build.solverVersion
    assert ready.solverVersion.endswith(".v1")
    artifact = SolverArtifact.model_validate_json(stand.artifacts.objects[metadataKey(ready.solverVersion)])
    assert artifact.source == "generated" and artifact.generation is not None
    crossCheck = json.loads(stand.artifacts.objects[artifact.tests.key])
    assert crossCheck["expected"] == {"exact": "2; 3"}
    assert [check["approach"] for check in crossCheck["checks"]] == ["independent"]
    # The model saw the task and nothing else.
    sent = [call.data.model_dump() for call in stand.transport.calls]
    assert {tuple(sorted(data)) for data in sent} == {
        ("approach", "constraints", "previousCode", "previousError", "statement")
    }
    assert all("ssignment" not in call.requestId for call in stand.transport.calls)
    # The same statement in another assignment reuses the version without a new model call.
    again = stand.provider.start(ProviderTask(assignmentRev=7, statement=QUADRATIC, constraints={}))
    assert again == ready and len(stand.transport.calls) == 2
    assert not build.log or all("statement" not in json.dumps(entry) for entry in build.log)


def testDisagreementIsSettledByATiebreak(sessionFactory: sessionmaker[Session]) -> None:
    transport = FixtureTransport(
        [],
        [author("d", "direct", code("42")), author("i", "independent", code("41")), author("t", "tiebreak", code("42"))],
    )
    stand = Stand(sessionFactory, transport)

    build = stand.build(task())
    ready = stand.provider.check(task())

    assert build.state == "published" and build.modelCalls == 3
    assert isinstance(ready, ReadyOutcome) and ready.value == {"exact": "42"}


def testNoAgreementGoesToTheTeacher(sessionFactory: sessionmaker[Session]) -> None:
    transport = FixtureTransport(
        [],
        [author("d", "direct", code("42")), author("i", "independent", code("41")), author("t", "tiebreak", code("40"))],
    )
    stand = Stand(sessionFactory, transport)

    build = stand.build(task())

    assert build.state == "failed" and build.reasonCode == "independent_tests_failed"
    assert stand.provider.check(task()) == RefusedOutcome(state="validation_failed", reasonCode="independent_tests_failed")
    assert stand.artifacts.objects == {}


def testSandboxErrorGoesBackToTheAuthor(sessionFactory: sessionmaker[Session]) -> None:
    broken = {"code": "def solve(task):\n    return {'exact': str(1 / 0)}\n"}
    transport = FixtureTransport(
        [],
        [
            author("d1", "direct", broken),
            author("d2", "direct", code("42"), retry=True),
            author("i", "independent", code("42")),
        ],
    )
    stand = Stand(sessionFactory, transport)

    build = stand.build(task())

    assert build.state == "published" and build.modelCalls == 3
    retried = transport.calls[1].data
    assert retried.previousError is not None and "ZeroDivisionError" in retried.previousError  # type: ignore[union-attr]
    assert [entry.get("outcome") for entry in build.log if "attempt" in entry] == ["sandbox_failed", "ok", "ok"]


def testAuthorOutOfAttemptsFailsTheBuild(sessionFactory: sessionmaker[Session]) -> None:
    transport = FixtureTransport(
        [], [author("d1", "direct", "не код"), author("d2", "direct", "всё ещё не код", retry=True)]
    )
    stand = Stand(sessionFactory, transport)

    build = stand.build(task())

    assert build.state == "failed" and build.reasonCode == "generation_failed" and build.modelCalls == 3
    assert stand.provider.check(task()) == RefusedOutcome(state="validation_failed", reasonCode="generation_failed")


class FlakyTransport:
    name = "fixture"

    def __init__(self, inner: FixtureTransport, failures: int) -> None:
        self.inner = inner
        self.failures = failures

    def complete(self, call: ModelCall) -> ModelReply:
        if self.failures:
            self.failures -= 1
            raise TransportError("rate_limited", "simulated 429", retryAfterS=40)
        return self.inner.complete(call)


def testRateLimitPausesTheBuildUntilItsDeadline(sessionFactory: sessionmaker[Session]) -> None:
    stand = Stand(sessionFactory)
    stand.author.transport = FlakyTransport(FixtureTransport.fromFile(), failures=1)  # type: ignore[assignment]

    paused = stand.build(task(QUADRATIC))
    assert paused.state == "queued" and not stand.builder.runOnce()
    stand.clock.advance(40)
    assert stand.builder.runOnce()

    assert isinstance(stand.provider.check(task(QUADRATIC)), ReadyOutcome)


def testAbandonedBuildTimesOut(sessionFactory: sessionmaker[Session]) -> None:
    stand = Stand(sessionFactory)

    assert isinstance(stand.provider.start(task()), PendingOutcome)
    stand.clock.advance(601)

    assert stand.provider.check(task()) == RefusedOutcome(state="validation_failed", reasonCode="generation_timeout")
    assert not stand.builder.runOnce()


def testPersonalDataNeverReachesTheModel(sessionFactory: sessionmaker[Session]) -> None:
    stand = Stand(sessionFactory)
    statement = "Иванов Иван Петрович купил 3 тетради по 5 рублей. Сколько он заплатил?"

    build = stand.build(task(statement))

    assert build.reasonCode == "personal_data_suspected" and stand.transport.calls == []
    assert stand.provider.check(task(statement)) == RefusedOutcome(
        state="unsupported", reasonCode="personal_data_suspected"
    )


def testRecordedFixturesStillAnswerFirst(sessionFactory: sessionmaker[Session]) -> None:
    stand = Stand(sessionFactory)

    outcome = stand.provider.start(task("Длина вектора (3; 4)"))

    assert isinstance(outcome, ReadyOutcome) and outcome.source == "fixture"
    with sessionFactory() as session:
        assert session.scalars(select(SolverBuildRecord)).first() is None


def testRegistryApiServesAGeneratedReference(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    settings = Settings(databaseUrl="unused", serviceTokensJson=TOKENS, provider="generating")
    client = TestClient(createApp(settings, sessionFactory=sessionFactory, clock=clock))
    body = {"assignmentId": "kr", "assignmentRev": 1, "taskId": "t1", "statement": QUADRATIC, "constraints": {}}

    posted = client.post("/internal/v1/references", json=body, headers=AUTH)
    provider = GeneratingProvider(FixtureProvider.fromFile(), sessionFactory, clock)
    builder = SolverBuilder(
        provider.store, SolverAuthor(FixtureTransport.fromFile(), LocalSandbox(), clock=clock), MemoryArtifactStore(),
        clock=clock,
    )
    assert builder.runOnce()
    clock.advance(posted.json()["retryAfterS"])
    ready = client.get(f"/internal/v1/references/{posted.json()['referenceId']}", headers=AUTH)

    assert posted.status_code == 202 and posted.json()["state"] == "pending"
    assert ready.status_code == 200
    assert ready.json()["source"] == "generated" and ready.json()["value"] == {"exact": "2; 3"}


def testProviderAndSandboxAreChosenExplicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFERENCES_PROVIDER", "gigachat")
    with pytest.raises(SettingsError):
        Settings.fromEnv()
    monkeypatch.delenv("REFERENCES_PROVIDER")
    assert Settings.fromEnv().provider == "fixture"
    from references.gateway.transports.base import ConfigError

    with pytest.raises(ConfigError):
        sandboxFromEnv({}, "fixture")
    with pytest.raises(ConfigError):
        sandboxFromEnv({"SOLVER_SANDBOX": "local"}, "gigachat")
    assert isinstance(sandboxFromEnv({"SOLVER_SANDBOX": "local"}, "fixture"), LocalSandbox)


def testAuthorStopsAtTheDeadline() -> None:
    clock = ManualClock()
    solverAuthor = SolverAuthor(FixtureTransport.fromFile(), LocalSandbox(), clock=clock)
    from references.solvers.author import BuildTimedOut

    with pytest.raises(BuildTimedOut):
        solverAuthor.write("b", AuthorTask(QUADRATIC, {}), "direct", clock.now(), AuthorLog())
