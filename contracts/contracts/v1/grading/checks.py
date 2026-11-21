"""Draft result read contract shared by grading and the cabinet (B5.1 and A7.1)."""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Discriminator, Field, SerializerFunctionWrapHandler, Tag, TypeAdapter, model_serializer

from contracts.v1.grading.assessment import AssessmentTotal, Grade, OcrStatus, PhotoMark, PhotoPage, Recommendation, TaskAssessment
from contracts.v1.grading.taskresult import NonEmptyStr, TaskResult

CHECKS_READ_RIGHT = "checks.read"
ResultRevision = Annotated[int, Field(ge=1, strict=True)]


class CheckIdentity(BaseModel):
    # Allow additive internal fields such as markdownRef without returning them from the cabinet.
    model_config = ConfigDict(extra="ignore", frozen=True)

    checkId: NonEmptyStr
    attemptId: NonEmptyStr
    transcriptMarkdown: str | None = None
    photoPages: list[PhotoPage] | None = None
    ocrStatus: OcrStatus | None = None
    ocrReason: NonEmptyStr | None = None
    grade: Grade | None = None
    """Only omit the grade upstream if the result has no revisioned grade field."""

    @model_serializer(mode="wrap")
    def serialize(self, handler: SerializerFunctionWrapHandler) -> dict[str, Any]:
        data: dict[str, Any] = handler(self)
        if "grade" in self.model_fields_set:
            data["grade"] = self.grade
        return data


class CheckPending(CheckIdentity):
    """A check without a result yet, a terminal failure, or a manual review without any draft to show."""

    state: Literal["queued", "waiting_reference", "running", "needs_review", "failed"]
    reason: NonEmptyStr


class CheckReady(CheckIdentity):
    """A completed result. needs_review means at least one task result is flagged for the teacher."""

    state: Literal["ready", "needs_review"]
    resultRev: ResultRevision
    publishedResultRev: ResultRevision | None = None
    """The published revision, if any. It can be older than the current resultRev."""
    taskResults: list[TaskResult]
    overallReview: NonEmptyStr
    taskAssessments: list[TaskAssessment] | None = None
    total: AssessmentTotal | None = None
    teacherComment: str | None = None
    recommendations: list[Recommendation] | None = None
    photoMarks: list[PhotoMark] | None = None


def checkKind(value: object) -> str:
    # needs_review exists with and without a result, so the result fields decide the variant.
    hasResult = isinstance(value, CheckReady) or (isinstance(value, dict) and "taskResults" in value)
    return "result" if hasResult else "status"


CheckStatus = Annotated[
    Annotated[CheckPending, Tag("status")] | Annotated[CheckReady, Tag("result")], Discriminator(checkKind)
]
checkStatusAdapter: TypeAdapter[CheckStatus] = TypeAdapter(CheckStatus)
