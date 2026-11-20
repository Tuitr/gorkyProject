"""Verify open generated submissions against fixed provider replies and their manifest."""

import json
from dataclasses import replace

import pytest

from contracts.v1.dataset.manifest import DatasetCase
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse, ReviewStep
from contracts.v1.references.reference import ReferenceReady
from dataset.generate import GeneratedDataset, generateDataset
from grading.assignment import AssignmentTask
from grading.intake import AttemptSubmittedInput, IncomingDocRef
from grading.review import reviewTasks
from grading.submission import readSubmissionDetails
from references.gateway.prompts import PromptCatalog
from references.gateway.sanitize import buildModelCall
from references.gateway.transports.fixture import FixtureTransport, ReviewFixture
from references.gateway.validation import toResponse


RECORDED_VERDICTS = {
    "demo1Student1Task1": ("correct", "valid", "optional"),
    "demo1Student1Task2": ("correct", "valid", "optional"),
    "demo1Student1Task3": ("correct", "valid", "optional"),
    # A fixed but overconfident reply. The worker only saw a final answer.
    "demo1Student2Task1": ("correct", "valid", "met"),
    "demo1Student2Task2": ("incorrect", "invalid", "optional"),
    "e2e1Student1Task1": ("correct", "valid", "optional"),
    "e2e1Student2Task1": ("correct", "invalid", "optional"),
}
RECORDED_ISSUES = {
    "demo1Student2Task2": [
        {"issueId": "i1", "stepId": "s1", "category": "arithmetic",
         "comment": "Ошибка в сумме координат.", "correction": "Пересчитайте сумму вторых координат."},
        {"issueId": "i2", "stepId": "s2", "category": "notation",
         "comment": "Сумма квадратов должна стоять под корнем.", "correction": "Возьмите сумму в скобки под корнем."},
        {"issueId": "i3", "stepId": "s3", "category": "answer", "causedByIssueId": "i1",
         "comment": "Ответ унаследовал ошибку сложения.", "correction": "Подставьте верную сумму координат."},
    ],
    "e2e1Student2Task1": [
        {"issueId": "i1", "stepId": "s2", "category": "sign",
         "comment": "Арифметический квадратный корень неотрицателен.", "correction": "Оставьте положительное значение."},
    ],
}
REQUIRED_METHOD = "Постройте вектор в масштабе и покажите геометрический способ."


@pytest.fixture(scope="module")
def reviewDataset() -> GeneratedDataset:
    return generateDataset()


def recordedReply(case: DatasetCase, variant: str) -> FixtureTransport:
    if case.caseId == "demo1Student2Task3":
        return FixtureTransport([])
    answer, reasoning, method = RECORDED_VERDICTS[case.caseId]
    if variant == "otherMethod":
        method = "unmet"
    issues = RECORDED_ISSUES.get(case.caseId, [])
    if variant == "foreignStep":
        issues = [{**issue, "stepId": "s99"} if issue["issueId"] == "i1" else issue for issue in issues]
    return FixtureTransport([ReviewFixture(
        fixtureId=case.caseId, taskId=f"t{case.assignmentRef.taskNumber}", statement=case.assignmentRef.statement,
        steps=tuple(ReviewStep.model_validate(step.model_dump()) for step in case.expectedSteps),
        content=json.dumps({"answer": answer, "reasoning": reasoning, "method": method, "issues": issues, "needsReview": False}),
    )])


@pytest.mark.parametrize(("caseId", "variant"), [
    *((caseId, "manifest") for caseId in (*RECORDED_VERDICTS, "demo1Student2Task3")),
    pytest.param("demo1Student1Task1", "otherMethod", id="valid-with-another-method"),
    pytest.param("demo1Student2Task1", "answerOnly", id="correct-without-required-method"),
    pytest.param("demo1Student2Task2", "foreignStep", id="foreign-step-and-dependent-issue"),
])
def testOpenDatasetReviewMatchesTheManifest(reviewDataset: GeneratedDataset, caseId: str, variant: str) -> None:
    openCases = [case for case in reviewDataset.manifest.cases if case.split in ("demo", "e2e")]
    case = next(case for case in openCases if case.caseId == caseId)
    tasks = tuple(
        AssignmentTask(f"t{ref.taskNumber}", ref.taskNumber, ref.statement, (), ref.constraints)
        for ref in (
            item.assignmentRef for item in openCases if item.submissionRef.file == case.submissionRef.file
        )
    )
    task = next(task for task in tasks if task.taskNumber == case.assignmentRef.taskNumber)
    if variant in ("otherMethod", "answerOnly"):
        task = replace(task, requirements=(REQUIRED_METHOD,))

    submitted = AttemptSubmittedInput(
        assignmentId=case.assignmentRef.file, assignmentRev=1, studentId="student", attemptId="attempt", checkId=case.caseId,
        docRef=IncomingDocRef(bucket="sources", key=case.submissionRef.file, sha256=case.submissionRef.sha256),
    )
    reading = readSubmissionDetails(submitted, reviewDataset.files[case.submissionRef.file], tasks)
    reference = case.expectedReference
    assert reference is not None
    refs = {task.taskId: ReferenceReady(
        state="ready", referenceId="reference", schemaId=reference.schemaId,
        value=reference.value, source="fixture", solverVersion=None,
    )}
    transport = recordedReply(case, variant)
    prompts = PromptCatalog()

    def review(request: ModelReviewRequest) -> ModelReviewResponse:
        call = buildModelCall(request, prompts)
        assert call.data.rubric == task.requirements
        return toResponse(request, transport.complete(call))

    result, = reviewTasks(case.caseId, (task,), reading, refs, review)
    expected = case.expectedVerdict
    method = {"otherMethod": "unmet", "answerOnly": "unconfirmed"}.get(variant, expected.method)
    assert (result.answer, result.reasoning, result.method) == (expected.answer, expected.reasoning, method)
    expectedIssues = case.expectedIssues
    if variant == "foreignStep":
        expectedIssues = [issue for issue in expectedIssues if issue.issueId != "i1" and issue.causedByIssueId != "i1"]
    assert [issue.model_dump(exclude={"comment", "correction"}) for issue in result.issues] == [
        issue.model_dump() for issue in expectedIssues
    ]
    assert result.needsReview == (expected.needsReview or variant == "foreignStep")
    assert len(transport.calls) == (0 if expected.answer == "skipped" else 1)
