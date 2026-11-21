"""The most recent non-deleted submission per student for one assignment revision."""

from pydantic import BaseModel, ConfigDict

from contracts.v1.events.checks import NonEmptyStr, Revision
from contracts.v1.grading.assessment import Count


class SubmissionSummary(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    studentId: NonEmptyStr
    checkId: NonEmptyStr
    attemptId: NonEmptyStr
    pageCount: Count
    """Number of original photos. A DOCX submission has no photo pages."""


class AssignmentSubmissions(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    submissions: list[SubmissionSummary]
