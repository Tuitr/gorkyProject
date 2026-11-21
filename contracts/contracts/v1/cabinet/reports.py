"""The cabinet report. The grading summary it is built from is contracts.v1.grading.assignmentreport."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from contracts.v1.cabinet.classes import StudentSummary
from contracts.v1.events.checks import NonEmptyStr, Revision
from contracts.v1.grading.assessment import Grade
from contracts.v1.grading.checks import ResultRevision

Count = Annotated[int, Field(ge=0, strict=True)]
Share = Annotated[float, Field(ge=0, le=1)]


class ReportResult(BaseModel):
    studentId: NonEmptyStr
    checkId: NonEmptyStr
    resultRev: ResultRevision
    grade: Grade | None = None
    """Grade from the selected result, or a legacy grade if that result has no grade field."""


class GradingTaskTotals(BaseModel):
    taskId: NonEmptyStr
    correct: Count
    incorrect: Count
    skipped: Count
    issuesByCategory: dict[NonEmptyStr, Count] = Field(default_factory=dict)


class ReportTask(GradingTaskTotals):
    unchecked: Count
    unreadable: Count = 0
    """Unreadable answers are a subset of unchecked."""
    correctShare: Share | None
    incorrectShare: Share | None
    skippedShare: Share | None


class ReportTrend(BaseModel):
    taskId: NonEmptyStr
    category: NonEmptyStr
    topic: NonEmptyStr
    exampleComment: NonEmptyStr


class AssignmentReport(BaseModel):
    assignmentId: NonEmptyStr
    assignmentRev: Revision
    students: list[StudentSummary]
    submittedCount: Count
    notSubmitted: list[StudentSummary]
    waiting: list[StudentSummary]
    results: list[ReportResult]
    tasks: list[ReportTask]
    trends: list[ReportTrend] = Field(default_factory=list)
