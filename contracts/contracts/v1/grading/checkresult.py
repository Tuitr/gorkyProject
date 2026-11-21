"""Read contract for the latest check result or a specific published revision."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from contracts.v1.events.checks import NonEmptyStr
from contracts.v1.grading.assessment import (
    AssessmentTotal, Grade, OcrStatus, PhotoBox as PhotoBox, Recommendation, TaskAssessment,
)
from contracts.v1.grading.taskresult import TaskResult


class MarkdownRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    bucket: Literal["results"]
    key: NonEmptyStr
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CheckOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    attemptId: str
    checkId: str
    resultRev: Annotated[int, Field(ge=1)]
    taskResults: list[TaskResult]
    overallReview: str
    taskAssessments: list[TaskAssessment] = Field(default_factory=list)
    total: AssessmentTotal | None = None
    recommendations: list[Recommendation] = Field(default_factory=list)
    grade: Grade | None = None
    teacherComment: str | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if "grade" not in self.model_fields_set:
            data.pop("grade", None)
        return data


class PhotoPage(BaseModel):
    pageIndex: int
    transcriptMarkdown: str


class PhotoMark(BaseModel):
    taskId: str
    issueId: str
    pageIndex: int
    lineIndex: int
    box: PhotoBox | None


class PhotoDetails(BaseModel):
    photoPages: list[PhotoPage] = Field(default_factory=list)
    photoMarks: list[PhotoMark] = Field(default_factory=list)
    ocrStatus: OcrStatus | None = None
    ocrReason: str | None = None


class PublishedCheck(CheckOutcome, PhotoDetails):
    state: Literal["ready", "needs_review"]
    markdownRef: MarkdownRef
    publishedResultRev: Annotated[int, Field(ge=1)] | None = None
    transcriptMarkdown: str | None = None
    """The latest revision the teacher published (accepted), if any. Filled only by the read API."""


class CheckStatus(PhotoDetails):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkId: str
    attemptId: str
    state: Literal["queued", "running", "waiting_reference", "needs_review", "failed"]
    reason: str
    transcriptMarkdown: str | None = None
