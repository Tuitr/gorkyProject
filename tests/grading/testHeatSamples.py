"""The physics sample (docs/samples/fizika-teplota): heat problems of grade 8, graded by the stand fixtures.

The teacher creates a physics assignment and uploads the ZIP of three works in fixture mode. The references come
from references.json (quantity.v1, constraints {"subject": "physics"}) and the model replies from reviews.json,
so both must match what grading reads from these files. The bank answers are recomputed here by hand.
"""

import hashlib
from fractions import Fraction

import pytest

from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from contracts.v1.references.quantity import parseQuantity
from contracts.v1.references.reference import ReferenceReady, ReferenceResult
from dataset.heat import BANK, SAMPLES, TEST, WORKS, sampleFiles
from grading.assignment import AssignmentTask, readAssignment
from grading.intake import AssignmentPublishedInput, AttemptSubmittedInput, IncomingDocRef
from grading.review import reviewTasks
from grading.submission import readSubmissionDetails
from references.gateway.prompts import PromptCatalog
from references.gateway.sanitize import buildModelCall
from references.gateway.transports.fixture import FixtureTransport
from references.gateway.validation import toResponse
from references.providers.base import ProviderTask, ReadyOutcome
from references.providers.fixture import FixtureProvider

EXPECTED = {
    "ivanova-olga": {"t1": "correct", "t2": "correct", "t3": "correct", "t4": "correct", "t5": "correct"},
    "smirnov-dmitriy": {"t1": "incorrect", "t2": "correct", "t3": "incorrect", "t4": "correct", "t5": "incorrect"},
    "kuznetsova-anna": {"t1": "incorrect", "t2": "incorrect", "t3": "correct", "t4": "correct", "t5": "incorrect"},
}
C_WATER, C_ICE, C_ALUMINIUM = 4200, 2100, 920
LAMBDA_ICE, LAMBDA_LEAD, L_WATER = Fraction(34 * 10**4), Fraction(25 * 10**3), Fraction(23 * 10**5)
Q_COAL = Q_ALCOHOL = Fraction(27 * 10**6)
BY_HAND = {
    "heating": C_WATER * 2 * (30 - 20),
    "cooling": C_ALUMINIUM * Fraction(1, 2) * (120 - 20),
    "melting": LAMBDA_ICE * 3,
    "boiling": L_WATER * Fraction(1, 5),
    "burning": Q_COAL * 5,
    "fuelMass": Fraction(54 * 10**5) / Q_ALCOHOL,
    "temperatureChange": Fraction(168_000, C_WATER * 4),
    "specificHeat": Fraction(9200, 2 * 10),
    "iceToWater": C_ICE * 2 * (0 - -10) + LAMBDA_ICE * 2,
    "mixing": Fraction(2 * 20 + 3 * 70, 2 + 3),
    "waterToSteam": C_WATER * 1 * (100 - 20) + L_WATER * 1,
    "efficiency": Fraction(C_WATER * Fraction(3, 10) * (80 - 20)) / (Q_ALCOHOL * Fraction(8, 1000)),
    "crystallization": LAMBDA_LEAD * Fraction(2, 5),
    "waterMass": Fraction(21 * 10**5, C_WATER * (60 - 10)),
}


def docRef(document: bytes, key: str) -> IncomingDocRef:
    return IncomingDocRef(bucket="sources", key=key, sha256=hashlib.sha256(document).hexdigest())


@pytest.fixture(scope="module")
def tasks() -> tuple[AssignmentTask, ...]:
    document = (SAMPLES / "teplovye-yavleniya-kr.docx").read_bytes()
    published = AssignmentPublishedInput(
        assignmentId="kr", assignmentRev=1, docRef=docRef(document, "kr"), requirements=[], subject="physics",
    )
    return readAssignment(published, document)


def references(tasks: tuple[AssignmentTask, ...]) -> dict[str, ReferenceResult]:
    provider = FixtureProvider.fromFile()
    found: dict[str, ReferenceResult] = {}
    for task in tasks:
        outcome = provider.start(ProviderTask(assignmentRev=1, statement=task.statement, constraints=task.constraints))
        assert isinstance(outcome, ReadyOutcome), task.taskId
        found[task.taskId] = ReferenceReady(
            state="ready", referenceId=task.taskId, schemaId=outcome.schemaId, value=outcome.value,
            source="fixture", solverVersion=None,
        )
    return found


def testSampleFilesAreUpToDate() -> None:
    """Regenerate with `uv run python -m dataset.heat` after changing tools/dataset/dataset/heat.py."""
    for name, body in sampleFiles().items():
        assert (SAMPLES / name).read_bytes() == body, name


@pytest.mark.parametrize("problem", BANK, ids=lambda problem: problem.problemId)
def testBankAnswersAreRight(problem: object) -> None:
    from dataset.heat import Problem

    assert isinstance(problem, Problem)
    quantity = parseQuantity(f"{problem.value} {problem.unit}")
    assert quantity is not None
    assert quantity.si == BY_HAND[problem.problemId], problem.problemId


def testPhysicsAssignmentTasksCarryTheSubject(tasks: tuple[AssignmentTask, ...]) -> None:
    assert [task.taskNumber for task in tasks] == ["1", "2", "3", "4", "5"]
    assert all(task.constraints == {"subject": "physics"} for task in tasks)
    found = references(tasks)
    answers = [found[task.taskId] for task in tasks]
    assert all(isinstance(answer, ReferenceReady) and answer.schemaId == "quantity.v1" for answer in answers)
    assert len(TEST) == len(tasks)


def testMathPublicationKeepsEmptyConstraints() -> None:
    document = (SAMPLES / "teplovye-yavleniya-kr.docx").read_bytes()
    published = AssignmentPublishedInput(assignmentId="kr", assignmentRev=1, docRef=docRef(document, "kr"), requirements=[])
    assert all(task.constraints == {} for task in readAssignment(published, document))


@pytest.mark.parametrize("student", EXPECTED)
def testRecordedReviewsGradeTheWork(tasks: tuple[AssignmentTask, ...], student: str) -> None:
    assert student in WORKS
    work = (SAMPLES / f"{student}.docx").read_bytes()
    submitted = AttemptSubmittedInput(
        assignmentId="kr", assignmentRev=1, studentId=student, attemptId=student, checkId=student,
        docRef=docRef(work, student),
    )
    reading = readSubmissionDetails(submitted, work, tasks)
    assert not reading.unassignedFragments
    transport = FixtureTransport.fromFile()
    prompts = PromptCatalog()
    sent: list[ModelReviewRequest] = []

    def review(request: ModelReviewRequest) -> ModelReviewResponse:
        sent.append(request)
        return toResponse(request, transport.complete(buildModelCall(request, prompts)))

    results: tuple[TaskResult, ...] = reviewTasks(student, tasks, reading, references(tasks), review)

    assert {result.taskId: result.answer for result in results} == EXPECTED[student]
    assert {request.promptVersion for request in sent} == {"review.v3"}
    assert all(request.reference.schemaId == "quantity.v1" for request in sent)
    for result in results:
        assert not result.needsReview
        assert bool(result.issues) == (result.answer == "incorrect")


def testPhysicsPromptExplainsUnitsAndTolerance() -> None:
    text = PromptCatalog().instructions("review.v3")
    for needle in ("relTolerance", "84 кДж = 84 000 Дж", "1000 Н = 1 кН", "missing_stage", "units"):
        assert needle in text
