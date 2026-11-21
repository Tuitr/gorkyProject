"""Teacher review changes are applied in order to one check result revision."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.v1.events.checks import NonEmptyStr, Revision
from contracts.v1.grading.assessment import Grade, Score
from contracts.v1.grading.taskresult import AnswerVerdict, MethodVerdict, ReasoningVerdict

RESULT_REV_HEADER = "X-Result-Rev"
"""Latest result revision on a conflict, including a revision awaiting its Markdown."""


class ReviewChange(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class RemoveIssue(ReviewChange):
    """Issue identifiers are scoped to a task. Removing a cause also removes its consequences."""

    op: Literal["removeIssue"]
    taskId: NonEmptyStr
    issueId: NonEmptyStr


class UpdateIssue(ReviewChange):
    op: Literal["updateIssue"]
    taskId: NonEmptyStr
    issueId: NonEmptyStr
    category: NonEmptyStr | None = None
    comment: NonEmptyStr | None = None
    correction: NonEmptyStr | None = None

    @model_validator(mode="after")
    def requireChange(self) -> Self:
        if self.category is None and self.comment is None and self.correction is None:
            raise ValueError("at least one issue field must change")
        return self


class SetVerdict(ReviewChange):
    op: Literal["setVerdict"]
    taskId: NonEmptyStr
    answer: AnswerVerdict | None = None
    reasoning: ReasoningVerdict | None = None
    method: MethodVerdict | None = None
    needsReview: bool | None = None

    @model_validator(mode="after")
    def requireChange(self) -> Self:
        if self.answer is None and self.reasoning is None and self.method is None and self.needsReview is None:
            raise ValueError("at least one verdict field must change")
        return self


class SetOverallReview(ReviewChange):
    op: Literal["setOverallReview"]
    overallReview: str


class SetGrade(ReviewChange):
    op: Literal["setGrade"]
    grade: Grade | None


class SetTeacherComment(ReviewChange):
    op: Literal["setTeacherComment"]
    teacherComment: NonEmptyStr | None


class SetTaskScore(ReviewChange):
    op: Literal["setTaskScore"]
    taskId: NonEmptyStr
    score: Score | None


EditChange = Annotated[
    RemoveIssue | UpdateIssue | SetVerdict | SetOverallReview | SetGrade | SetTeacherComment | SetTaskScore,
    Field(discriminator="op"),
]



class ReviewEditRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    baseRev: Revision
    editId: NonEmptyStr
    actorId: NonEmptyStr
    changes: Annotated[list[EditChange], Field(min_length=1)]


class ReviewEditReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkId: NonEmptyStr
    editId: NonEmptyStr
    resultRev: Revision
