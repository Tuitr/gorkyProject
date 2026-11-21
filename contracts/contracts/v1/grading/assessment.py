"""Assessment data returned alongside a check's task verdicts."""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from contracts.v1.events.checks import NonEmptyStr

Count = Annotated[int, Field(ge=0, strict=True)]
Score = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]
MaximumScore = Annotated[float, Field(gt=0, allow_inf_nan=False, strict=True)]
Grade = Annotated[int, Field(ge=2, le=5, strict=True)]
OcrStatus = Literal["readable", "needs_review", "unreadable"]


class AssessmentData(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class TaskAssessment(AssessmentData):
    taskId: NonEmptyStr
    score: Score | None
    maxScore: MaximumScore
    comment: NonEmptyStr

    @model_validator(mode="after")
    def scoreWithinMaximum(self) -> Self:
        if self.score is not None and self.score > self.maxScore:
            raise ValueError("Score exceeds the task maximum")
        return self


class AssessmentTotal(AssessmentData):
    score: Score
    maxScore: Score
    pendingCount: Count

    @model_validator(mode="after")
    def scoreWithinMaximum(self) -> Self:
        if self.score > self.maxScore:
            raise ValueError("Score exceeds the assignment maximum")
        return self


class Recommendation(AssessmentData):
    topic: NonEmptyStr
    text: NonEmptyStr


class PhotoPage(AssessmentData):
    pageIndex: Count
    transcriptMarkdown: str | None = None
    url: NonEmptyStr | None = None
    """The cabinet generates this URL from the original upload."""


class PhotoBox(AssessmentData):
    x: Annotated[float, Field(ge=0, lt=1)]
    y: Annotated[float, Field(ge=0, lt=1)]
    w: Annotated[float, Field(gt=0, le=1)]
    h: Annotated[float, Field(gt=0, le=1)]

    @model_validator(mode="after")
    def withinPage(self) -> Self:
        if self.x + self.w > 1 or self.y + self.h > 1:
            raise ValueError("Box exceeds the photo boundaries")
        return self


class PhotoMark(AssessmentData):
    taskId: NonEmptyStr
    issueId: NonEmptyStr
    pageIndex: Count
    lineIndex: Count
    box: PhotoBox | None = None
