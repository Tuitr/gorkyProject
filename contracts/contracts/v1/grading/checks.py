"""Draft result read contract shared by grading and the cabinet (B5.1 and A7.1)."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from contracts.v1.grading.taskresult import NonEmptyStr, TaskResult

CHECKS_READ_RIGHT = "checks.read"
ResultRevision = Annotated[int, Field(ge=1, strict=True)]


class CheckIdentity(BaseModel):
    # Allow additive internal fields such as markdownRef without returning them from the cabinet.
    model_config = ConfigDict(extra="ignore", frozen=True)

    checkId: NonEmptyStr
    attemptId: NonEmptyStr


class CheckPending(CheckIdentity):
    """A check without a ready result, including terminal failures and cases for manual review."""

    state: Literal["queued", "waiting_reference", "running", "needs_review", "failed"]
    reason: NonEmptyStr


class CheckReady(CheckIdentity):
    state: Literal["ready"]
    resultRev: ResultRevision
    publishedResultRev: ResultRevision | None = None
    """The published revision, if any. It can be older than the current resultRev."""
    taskResults: list[TaskResult]
    overallReview: NonEmptyStr


CheckStatus = Annotated[CheckPending | CheckReady, Field(discriminator="state")]
checkStatusAdapter: TypeAdapter[CheckStatus] = TypeAdapter(CheckStatus)
