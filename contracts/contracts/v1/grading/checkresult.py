"""Read contract for the latest check result or a specific published revision."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from contracts.v1.events.checks import NonEmptyStr
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


class PhotoBox(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    x: float
    y: float
    w: float
    h: float

    def fitsPage(self) -> bool:
        return 0 <= self.x < 1 and 0 <= self.y < 1 and 0 < self.w <= 1 - self.x and 0 < self.h <= 1 - self.y


class PhotoPage(BaseModel):
    pageIndex: int
    transcriptMarkdown: str


class PhotoMark(BaseModel):
    taskId: str
    issueId: str
    pageIndex: int
    lineIndex: int
    box: PhotoBox | None


OcrStatus = Literal["readable", "needs_review", "unreadable"]


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
