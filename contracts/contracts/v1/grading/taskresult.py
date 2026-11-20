"""taskResult: the verdict for one task of one attempt.

Owner: developer 2. Consumers: developer 1 (cabinet), developer 3 (model gateway validates the shape).
Confirmed: developer 2 adopted it for review (grading/review.py, B4.2) and owns any change from now on.
The gateway validates model answers against this class and must not keep its own copy.

Answer, reasoning and required method are separate dimensions: a correct answer can come with
reasoning=not_shown and method=unconfirmed.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

NonEmptyStr = Annotated[str, Field(min_length=1)]

AnswerVerdict = Literal["correct", "incorrect", "skipped", "unreadable"]
ReasoningVerdict = Literal["valid", "invalid", "not_shown", "unknown"]
MethodVerdict = Literal["met", "unmet", "unconfirmed", "optional"]


class Issue(BaseModel):
    """One error at one step. A consequence points to its root error through causedByIssueId."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    issueId: NonEmptyStr
    stepId: NonEmptyStr
    category: NonEmptyStr
    comment: NonEmptyStr
    correction: NonEmptyStr
    causedByIssueId: NonEmptyStr | None = None


class TaskResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    taskId: NonEmptyStr
    answer: AnswerVerdict
    reasoning: ReasoningVerdict
    method: MethodVerdict
    issues: list[Issue]
    needsReview: bool
