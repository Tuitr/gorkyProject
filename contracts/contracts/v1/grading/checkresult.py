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


class PublishedCheck(CheckOutcome):
    state: Literal["ready", "needs_review"]
    markdownRef: MarkdownRef
    publishedResultRev: Annotated[int, Field(ge=1)] | None = None
    transcriptMarkdown: str | None = None
    """The latest revision the teacher published (accepted), if any. Filled only by the read API."""


class CheckStatus(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkId: str
    attemptId: str
    state: Literal["queued", "running", "waiting_reference", "needs_review", "failed"]
    reason: str
    transcriptMarkdown: str | None = None
