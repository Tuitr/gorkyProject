import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from contracts.v1 import openapi
from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"


def readExample(name: str) -> Any:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def testModelReviewRequestExample() -> None:
    request = ModelReviewRequest.model_validate(readExample("request.json"))

    assert [step.stepId for step in request.steps] == ["s1"]
    assert request.reference.value == {"exact": "5"}


def testAnswerOnlyKeepsThreeVerdictsSeparate() -> None:
    response = ModelReviewResponse.model_validate(readExample("responseAnswerOnly.json"))

    verdicts = (response.taskResult.answer, response.taskResult.reasoning, response.taskResult.method)
    assert verdicts == ("correct", "not_shown", "unconfirmed")


def testIssuesLinkConsequenceToRootError() -> None:
    response = ModelReviewResponse.model_validate(readExample("responseWithIssues.json"))

    links = {issue.issueId: issue.causedByIssueId for issue in response.taskResult.issues}
    assert links == {"i1": None, "i2": "i1", "i3": None}


def testGatewayUsesTheGradingTaskResult() -> None:
    assert ModelReviewResponse.model_fields["taskResult"].annotation is TaskResult


@pytest.mark.parametrize(
    "fileName",
    sorted(path.name for path in EXAMPLES.glob("response*.invalid.json")),
)
def testInvalidResponsesAreRejected(fileName: str) -> None:
    with pytest.raises(ValidationError):
        ModelReviewResponse.model_validate(readExample(fileName))


def testRequestWithoutStepsIsRejected() -> None:
    example = readExample("request.json") | {"steps": []}

    with pytest.raises(ValidationError):
        ModelReviewRequest.model_validate(example)


@pytest.mark.parametrize("fileName", ["responseAnswerOnly.json", "responseWithIssues.json"])
def testResponseRoundTrip(fileName: str) -> None:
    response = ModelReviewResponse.model_validate(readExample(fileName))

    wire = json.loads(response.model_dump_json(exclude_none=False))
    assert ModelReviewResponse.model_validate(wire) == response
