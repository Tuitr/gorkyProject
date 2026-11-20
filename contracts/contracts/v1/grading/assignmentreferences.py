"""Reference answers of one assignment revision, as grading prepared them (developer 3's registry).

GET /internal/v1/assignments/{assignmentId}/references?assignmentRev=N in grading, and the same body from the
cabinet's GET /v1/assignments/{assignmentId}/references?rev=N after the owner check. The teacher sees which
answer each task is checked against before any work arrives. A reference that is not ready names why.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, JsonValue

from contracts.v1.events.checks import NonEmptyStr, Revision

PreparationState = Literal["queued", "running", "waiting_reference", "ready", "needs_review", "failed"]
ReferenceState = Literal["missing", "pending", "ready", "unsupported", "validation_failed"]


class TaskReference(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    taskId: NonEmptyStr
    taskNumber: str
    statement: str
    state: ReferenceState
    answer: str | None = None
    """A ready scalar.v1 answer as written, for example "13" or "-3/4"."""
    schemaId: str | None = None
    value: dict[str, JsonValue] | None = None
    reasonCode: str | None = None


class AssignmentReferences(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    state: PreparationState
    """The assignment job: tasks are listed once the variant is parsed."""
    tasks: list[TaskReference]
