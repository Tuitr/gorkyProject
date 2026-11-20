"""Physics tasks get a solver that answers with a number and a unit (quantity.v1), admitted by a unit-aware cross-check.

The subject travels in the reference constraints ({"subject": "physics"}); math tasks keep empty constraints.
Everything runs on the fixture transport and the local sandbox.
"""

from pydantic import JsonValue
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.references.solverartifact import SolverArtifact, metadataKey
from dataset.heat import BY_ID
from references.gateway.transports.fixture import AuthorFixture, FixtureTransport
from references.providers.base import PendingOutcome, ProviderTask, ReadyOutcome
from references.providers.fixture import FixtureProvider
from references.providers.generating import GeneratingProvider
from references.solvers.answers import answerOf, schemaOf, valuesAgree
from references.solvers.artifacts import MemoryArtifactStore
from references.solvers.author import SolverAuthor
from references.solvers.builder import SolverBuilder
from references.solvers.sandbox import LocalSandbox
from references.solvers.tables import SolverBuildRecord
from shared.clock import ManualClock

PHYSICS: dict[str, JsonValue] = {"subject": "physics"}
STEAM = BY_ID["waterToSteam"].statement
NEW_TASK = "Сколько теплоты нужно, чтобы нагреть 1 кг воды на 1 °C?"


def physicsTask(statement: str) -> ProviderTask:
    return ProviderTask(assignmentRev=1, statement=statement, constraints=PHYSICS)


def author(fixtureId: str, approach: str, value: object, unit: str) -> AuthorFixture:
    code = f"def solve(task):\n    return {{'value': {value!r}, 'unit': {unit!r}}}\n"
    return AuthorFixture.model_validate(
        {"fixtureId": fixtureId, "statement": NEW_TASK, "approach": approach, "content": {"code": code}}
    )


class Stand:
    def __init__(self, sessionFactory: sessionmaker[Session], transport: FixtureTransport | None = None) -> None:
        self.clock = ManualClock()
        self.transport = transport if transport is not None else FixtureTransport.fromFile()
        self.artifacts = MemoryArtifactStore()
        self.sessionFactory = sessionFactory
        self.provider = GeneratingProvider(FixtureProvider.fromFile(), sessionFactory, self.clock, buildDeadlineS=600)
        self.builder = SolverBuilder(
            self.provider.store, SolverAuthor(self.transport, LocalSandbox(), clock=self.clock), self.artifacts,
            clock=self.clock,
        )

    def build(self, task: ProviderTask) -> SolverBuildRecord:
        assert isinstance(self.provider.start(task), PendingOutcome)
        assert self.builder.runOnce()
        with self.sessionFactory() as session:
            return session.scalars(select(SolverBuildRecord)).one()


def testSubjectChoosesTheValueSchema() -> None:
    assert schemaOf({}) == "scalar.v1"
    assert schemaOf(PHYSICS) == "quantity.v1"
    assert schemaOf({"subject": "chemistry"}) == "scalar.v1"


def testSolverValueIsNormalized() -> None:
    assert answerOf("quantity.v1", {"value": 2635999.9999999995, "unit": "Дж"}) == {
        "value": "2636000", "unit": "Дж", "relTolerance": 0.02,
    }
    assert answerOf("quantity.v1", {"value": "2,7·10⁷", "unit": "Дж/кг"}) == {
        "value": "27000000", "unit": "Дж/кг", "relTolerance": 0.02,
    }
    assert answerOf("quantity.v1", {"value": 0.35, "unit": ""}) == {"value": "0.35", "unit": "", "relTolerance": 0.02}
    broken: list[dict[str, JsonValue]] = [
        {"exact": "5"}, {"value": "5", "unit": "попугай"}, {"value": True, "unit": "Дж"}, {"value": "5"},
    ]
    for value in broken:
        assert answerOf("quantity.v1", value) is None


def testSolversAgreeAcrossUnitsButNotAcrossPhysics() -> None:
    def value(number: str, unit: str) -> dict[str, JsonValue]:
        result = answerOf("quantity.v1", {"value": number, "unit": unit})
        assert result is not None
        return result

    assert valuesAgree("quantity.v1", value("84000", "Дж"), value("84", "кДж"))
    assert valuesAgree("quantity.v1", value("35", "%"), value("0.35", ""))
    # 9,8 Н/кг against 10 Н/кг is a 2 % gap: a disagreement about the data, not rounding.
    assert not valuesAgree("quantity.v1", value("98", "Н"), value("100", "Н"))
    assert not valuesAgree("quantity.v1", value("84000", "Дж"), value("84000", "Вт"))


def testPhysicsTaskGetsAQuantityReference(sessionFactory: sessionmaker[Session]) -> None:
    stand = Stand(sessionFactory)

    build = stand.build(physicsTask(STEAM))
    ready = stand.provider.check(physicsTask(STEAM))

    assert build.state == "published" and build.modelCalls == 2
    assert isinstance(ready, ReadyOutcome) and ready.source == "generated"
    assert ready.schemaId == "quantity.v1"
    assert ready.value == {"value": "2636000", "unit": "Дж", "relTolerance": 0.02}
    assert {call.promptVersion for call in stand.transport.calls} == {"solverPhysics.v1"}
    assert ready.solverVersion is not None
    artifact = SolverArtifact.model_validate_json(stand.artifacts.objects[metadataKey(ready.solverVersion)])
    assert artifact.scope.schemaId == "quantity.v1" and artifact.scope.constraints == {"subject": ["physics"]}


def testSameStatementAsMathIsAnotherTask(sessionFactory: sessionmaker[Session]) -> None:
    """The subject is part of the task key: a math request with the same text does not reuse the physics solver."""
    stand = Stand(sessionFactory)
    stand.build(physicsTask(STEAM))

    math = stand.provider.start(ProviderTask(assignmentRev=1, statement=STEAM, constraints={}))

    assert isinstance(math, PendingOutcome)


def testKilojoulesAndJoulesAreOneAnswer(sessionFactory: sessionmaker[Session]) -> None:
    transport = FixtureTransport([], [author("d", "direct", 4200, "Дж"), author("i", "independent", "4,2", "кДж")])
    stand = Stand(sessionFactory, transport)

    build = stand.build(physicsTask(NEW_TASK))

    assert build.state == "published" and build.modelCalls == 2
    ready = stand.provider.check(physicsTask(NEW_TASK))
    assert isinstance(ready, ReadyOutcome) and ready.value == {"value": "4200", "unit": "Дж", "relTolerance": 0.02}


def testAnotherConstantIsADisagreement(sessionFactory: sessionmaker[Session]) -> None:
    transport = FixtureTransport(
        [],
        [
            author("d", "direct", 4200, "Дж"),
            author("i", "independent", 4000, "Дж"),
            author("t", "tiebreak", 4200, "Дж"),
        ],
    )
    stand = Stand(sessionFactory, transport)

    build = stand.build(physicsTask(NEW_TASK))

    assert build.state == "published" and build.modelCalls == 3


def testWrongShapeGoesBackToTheAuthor(sessionFactory: sessionmaker[Session]) -> None:
    exact = "def solve(task):\n    return {'exact': '4200'}\n"
    transport = FixtureTransport(
        [],
        [
            AuthorFixture.model_validate(
                {"fixtureId": "d0", "statement": NEW_TASK, "approach": "direct", "content": {"code": exact}}
            ),
            author("d", "direct", 4200, "Дж").model_copy(update={"retry": True}),
            author("i", "independent", 4200, "Дж"),
        ],
    )
    stand = Stand(sessionFactory, transport)

    build = stand.build(physicsTask(NEW_TASK))

    assert build.state == "published" and build.modelCalls == 3
    retried = transport.calls[1].data
    assert getattr(retried, "previousError", "") is not None and '"unit"' in str(getattr(retried, "previousError"))
