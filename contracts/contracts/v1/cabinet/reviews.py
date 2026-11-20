"""Public teacher review operations and the grading receipts the cabinet passes on."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.v1.common.errors import ApiError
from contracts.v1.grading.checks import ResultRevision
from contracts.v1.grading.taskresult import AnswerVerdict, MethodVerdict, NonEmptyStr, ReasoningVerdict


class ReviewInput(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RemoveIssue(ReviewInput):
    """Issue ids are unique within a task. Without taskId the cabinet finds the only task holding the id."""

    type: Literal["remove_issue"]
    issueId: NonEmptyStr
    taskId: NonEmptyStr | None = None


class UpdateIssue(ReviewInput):
    type: Literal["update_issue"]
    issueId: NonEmptyStr
    taskId: NonEmptyStr | None = None
    comment: NonEmptyStr
    correction: NonEmptyStr


class UpdateVerdict(ReviewInput):
    type: Literal["update_verdict"]
    taskId: NonEmptyStr
    answer: AnswerVerdict | None = None
    reasoning: ReasoningVerdict | None = None
    method: MethodVerdict | None = None

    @model_validator(mode="after")
    def requireVerdict(self) -> Self:
        if self.answer is None and self.reasoning is None and self.method is None:
            raise ValueError("At least one verdict is required")
        return self


class UpdateOverallReview(ReviewInput):
    type: Literal["update_overall_review"]
    overallReview: NonEmptyStr


ReviewChange = Annotated[
    RemoveIssue | UpdateIssue | UpdateVerdict | UpdateOverallReview, Field(discriminator="type")
]


class EditReview(ReviewInput):
    baseRev: ResultRevision
    editId: NonEmptyStr
    changes: Annotated[list[ReviewChange], Field(min_length=1)]


class PublishReview(ReviewInput):
    resultRev: ResultRevision


class ReviewRevision(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    checkId: NonEmptyStr
    resultRev: ResultRevision


class ReviewEdited(ReviewRevision):
    editId: NonEmptyStr


class ReviewConflict(ApiError):
    model_config = ConfigDict(extra="ignore", frozen=True)

    code: Literal["conflict"]
    resultRev: ResultRevision
