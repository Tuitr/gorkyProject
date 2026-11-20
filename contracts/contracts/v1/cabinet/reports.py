"""The cabinet report and the draft summary it consumes from grading."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from contracts.v1.cabinet.classes import StudentSummary
from contracts.v1.events.checks import NonEmptyStr, Revision
from contracts.v1.grading.checks import ResultRevision

Count = Annotated[int, Field(ge=0, strict=True)]
Share = Annotated[float, Field(ge=0, le=1)]


class ReportResult(BaseModel):
    studentId: NonEmptyStr
    checkId: NonEmptyStr
    resultRev: ResultRevision


class GradingTaskTotals(BaseModel):
    taskId: NonEmptyStr
    correct: Count
    incorrect: Count
    skipped: Count
    issuesByCategory: dict[NonEmptyStr, Count] = Field(default_factory=dict)


class GradingReport(BaseModel):
    """One selected check/revision per student. Task counts refer to these selected results."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    results: list[ReportResult]
    tasks: list[GradingTaskTotals]


class ReportTask(GradingTaskTotals):
    unchecked: Count
    correctShare: Share | None
    incorrectShare: Share | None
    skippedShare: Share | None


class AssignmentReport(BaseModel):
    assignmentId: NonEmptyStr
    assignmentRev: Revision
    students: list[StudentSummary]
    submittedCount: Count
    notSubmitted: list[StudentSummary]
    waiting: list[StudentSummary]
    results: list[ReportResult]
    tasks: list[ReportTask]
