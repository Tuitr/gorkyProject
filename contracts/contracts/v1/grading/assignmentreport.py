"""Class report for the cabinet.

Without rev, GET reads the current assignment revision and returns its latest
snapshot. With rev, GET returns exactly that saved snapshot. Each student row
points to the completed result revision used in the counts. Draft attempts
are ordered by submission time. Teacher acceptance takes precedence and is
ordered by acceptance time. The selected attempt contributes its latest
completed result, including completed teacher edits.

Shares here use completed students. The cabinet adds the class roster and
pending submissions to form its public denominator. A zero denominator yields
null. See examples/grading/assignmentReport.json.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from contracts.v1.events.checks import NonEmptyStr, Revision
from contracts.v1.grading.assessment import Grade
from contracts.v1.grading.taskresult import AnswerVerdict, MethodVerdict, ReasoningVerdict

Count = Annotated[int, Field(ge=0)]
Share = Annotated[float, Field(ge=0, le=1)]


class ReportStudent(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    studentId: NonEmptyStr
    attemptId: NonEmptyStr
    checkId: NonEmptyStr
    resultRev: Revision
    selection: Literal["accepted", "draft"]
    grade: Grade | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if "grade" not in self.model_fields_set:
            data.pop("grade", None)
        return data


class CategoryTrend(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    category: NonEmptyStr
    affectedStudents: Count
    primaryIssues: Count
    topic: NonEmptyStr | None = None
    exampleComment: NonEmptyStr | None = None

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if self.topic is None:
            data.pop("topic", None)
        if self.exampleComment is None:
            data.pop("exampleComment", None)
        return data


class TaskDistribution(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    taskId: NonEmptyStr
    answer: dict[AnswerVerdict, Count]
    reasoning: dict[ReasoningVerdict, Count]
    method: dict[MethodVerdict, Count]
    needsReview: Count
    missing: Count
    correctShare: Share | None
    categories: list[CategoryTrend]


class AssignmentReport(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    rev: Revision
    studentCount: Count
    students: list[ReportStudent]
    tasks: list[TaskDistribution]
