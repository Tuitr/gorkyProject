"""Worker input from checks.v1: the `data` field read into the events the grading worker acts on.

Publisher: developer 1, schema contracts/v1/schemas/checks.json. Consumer: developer 2 (group grading.v1).

The publisher schema is strict. The worker reads with its own models: unknown optional fields from a newer
publisher are ignored (contracts v1 compatibility), required fields and value rules stay the same.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter

from contracts.v1.events.checks import NonEmptyStr, Revision, UtcTimestamp


class IncomingDocRef(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    bucket: NonEmptyStr
    key: NonEmptyStr
    sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class IncomingRequirement(BaseModel):
    """taskNumber is the number printed in the DOCX; None means the whole assignment."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    text: NonEmptyStr
    taskNumber: NonEmptyStr | None = None


class AssignmentPublishedInput(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    docRef: IncomingDocRef
    requirements: list[IncomingRequirement]


class AttemptSubmittedInput(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    studentId: NonEmptyStr
    attemptId: NonEmptyStr
    checkId: NonEmptyStr
    docRef: IncomingDocRef


class IncomingEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    v: Literal[1]
    eventId: NonEmptyStr
    traceId: NonEmptyStr
    occurredAt: UtcTimestamp


class AssignmentPublished(IncomingEnvelope):
    type: Literal["assignment.published"]
    payload: AssignmentPublishedInput


class AttemptSubmitted(IncomingEnvelope):
    type: Literal["attempt.submitted"]
    payload: AttemptSubmittedInput


IncomingEvent = Annotated[AssignmentPublished | AttemptSubmitted, Field(discriminator="type")]
incomingEventAdapter: TypeAdapter[IncomingEvent] = TypeAdapter(IncomingEvent)


def readEvent(data: str | bytes) -> IncomingEvent:
    """Parse the `data` field of one stream entry. Raises pydantic.ValidationError for a malformed event."""
    return incomingEventAdapter.validate_json(data)
