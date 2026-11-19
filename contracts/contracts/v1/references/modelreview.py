"""POST /internal/v1/model/review: one task of one attempt checked by the model.

Owner of the envelope: developer 3. The prompt, rubric meaning and taskResult content belong
to developer 2; taskResult is imported from contracts.v1.grading and never copied here.

The request carries only mathematical content: no student name, file name, document metadata
or closed dataset answers.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.reference import VALUE_SCHEMAS

NonEmptyStr = Annotated[str, Field(min_length=1)]

TASK_RESULT_SCHEMA_VERSION = "taskResult.v1"


class ReviewStep(BaseModel):
    """One line of the student's solution, already normalized to Markdown."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    stepId: NonEmptyStr
    markdown: str


class ReviewReference(BaseModel):
    """The ready reference the model compares the solution with."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    referenceId: NonEmptyStr
    schemaId: NonEmptyStr
    value: dict[str, JsonValue]

    @model_validator(mode="after")
    def checkValue(self) -> Self:
        valueSchema = VALUE_SCHEMAS.get(self.schemaId)
        if valueSchema is None:
            raise ValueError(f"unknown schemaId {self.schemaId!r}")
        valueSchema.model_validate(self.value)
        return self


class ModelReviewRequest(BaseModel):
    """Repeating the same requestId with the same body returns the stored answer; another body is 409."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requestId: NonEmptyStr
    promptVersion: NonEmptyStr
    taskId: NonEmptyStr
    statement: NonEmptyStr
    rubric: list[NonEmptyStr]
    steps: Annotated[list[ReviewStep], Field(min_length=1)]
    reference: ReviewReference


class ModelReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["taskResult.v1"]
    taskResult: TaskResult
    modelId: NonEmptyStr
    providerRequestId: NonEmptyStr | None = None
