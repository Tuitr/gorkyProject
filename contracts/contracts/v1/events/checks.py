"""Events of the Redis stream checks.v1: the JSON in the stream field `data`.

Owner: developer 1 (platform outbox). Consumer: developer 2 (grading worker, group grading.v1).

The envelope {v, eventId, type, traceId, occurredAt, payload} is shared; `type` selects the payload schema.
The stream carries references to immutable S3 objects, never the content of a work.
"""

from datetime import datetime, timedelta
from typing import Annotated, Any, Final, Literal

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    TypeAdapter,
)

STREAM_NAME: Final = "checks.v1"
DATA_FIELD: Final = "data"
CONSUMER_GROUP: Final = "grading.v1"

NonEmptyStr = Annotated[str, Field(min_length=1)]
Revision = Annotated[int, Field(ge=1, strict=True)]
"""Assignment revision: an integer from 1, never a string or a float."""


def rejectNumericTime(value: Any) -> Any:
    if isinstance(value, (int, float)):
        raise ValueError("occurredAt must be an RFC 3339 string, not a number")
    return value


def requireUtc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("occurredAt must be in UTC")
    return value


UtcTimestamp = Annotated[AwareDatetime, BeforeValidator(rejectNumericTime), AfterValidator(requireUtc)]
"""RFC 3339 in UTC, serialized as 2026-11-20T09:30:00Z."""


class DocRef(BaseModel):
    """Immutable DOCX in S3. A new upload gets a new key; sha256 lets the reader verify the bytes."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bucket: NonEmptyStr
    key: NonEmptyStr
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class TeacherRequirement(BaseModel):
    """Teacher requirement for the method. taskNumber is the number printed in the DOCX; null means the whole assignment."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    text: NonEmptyStr
    taskNumber: NonEmptyStr | None = None


class AssignmentPublishedPayload(BaseModel):
    """A new revision of an assignment is ready: the worker may parse its tasks and request references."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    docRef: DocRef
    requirements: list[TeacherRequirement]


class AttemptSubmittedPayload(BaseModel):
    """A student uploaded a work for one assignment revision: the worker starts check checkId."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    studentId: NonEmptyStr
    attemptId: NonEmptyStr
    checkId: NonEmptyStr
    docRef: DocRef


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    v: Literal[1]
    eventId: NonEmptyStr
    traceId: NonEmptyStr
    occurredAt: UtcTimestamp


class AssignmentPublishedEvent(EventEnvelope):
    type: Literal["assignment.published"]
    payload: AssignmentPublishedPayload


class AttemptSubmittedEvent(EventEnvelope):
    type: Literal["attempt.submitted"]
    payload: AttemptSubmittedPayload


ChecksEvent = Annotated[AssignmentPublishedEvent | AttemptSubmittedEvent, Field(discriminator="type")]
checksEventAdapter: TypeAdapter[ChecksEvent] = TypeAdapter(ChecksEvent)
