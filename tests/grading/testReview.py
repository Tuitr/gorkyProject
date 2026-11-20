"""Check the review request body and transport boundary."""

import pytest

from contracts.v1.references.modelreview import ReviewStep
from contracts.v1.references.reference import ReferenceReady
from grading.assignment import AssignmentTask
from grading.intake import IncomingDocRef
from grading.review import buildReviewRequest, reviewTasks
from grading.submission import SolutionStep, SourceAnchor, SubmissionReading, TaskReading


REF = ReferenceReady(referenceId="r1", state="ready", source="fixture", schemaId="scalar.v1",
                     value={"exact": "10"}, solverVersion=None)
ANCHOR = SourceAnchor(IncomingDocRef(bucket="sources", key="STUDENT_MARKER.docx", sha256="a" * 64), (), 0, 0, 1)
TASK = AssignmentTask("t1", "1", "Вычислите 2 × 5.", ())


STEPS = [ReviewStep(stepId=f"s{i}", markdown=line) for i, line in enumerate(
    ("(−3; 4) = (3; 4)", "Длина = 3 + 4", "Ответ: 7"), 1)]


def testVersionedBodyContainsOnlyContractFields() -> None:
    request = buildReviewRequest("STUDENT_MARKER", TASK, STEPS, REF)
    assert set(request.model_dump()) == {"requestId", "taskId", "promptVersion", "rubric", "steps", "reference", "statement"}
    assert request.steps == STEPS and "STUDENT_MARKER" not in request.model_dump_json()
    assert request == buildReviewRequest("STUDENT_MARKER", TASK, STEPS, REF)
    assert request.requestId != buildReviewRequest("STUDENT_MARKER", TASK, STEPS, REF, "review.v3").requestId
    assert request.requestId != buildReviewRequest("STUDENT_MARKER", TASK, STEPS[:-1], REF).requestId


def testEmbeddedSignatureGoesToTeacherBeforeTransport() -> None:
    line = SolutionStep("t1", "s1", "Ответ: 10. Выполнил: STUDENT_MARKER", ANCHOR, "read")
    reading = SubmissionReading((line,), (TaskReading("t1", "answerOnly"),), ())
    result, = reviewTasks("check", (TASK,), reading, {"t1": REF}, lambda _: pytest.fail("unexpected model call"))
    assert result.needsReview and not result.issues
