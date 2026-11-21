"""Worker input from checks.v1: the `data` field read into the events the grading worker acts on.

Publisher: developer 1, schema contracts/v1/schemas/checks.json. Consumer: developer 2 (group grading.v1).

The publisher schema is strict. The worker reads with its own models: unknown optional fields from a newer
publisher are ignored (contracts v1 compatibility), required fields and value rules stay the same.

Boundary: raw bytes or str from the stream go only into readEvent; handlers receive a validated IncomingEvent.
A rejected entry raises MalformedEvent (not readable JSON) or InvalidEvent (JSON that breaks the schema)
before any business processing. Rejection messages name fields and error kinds, never the input values.
"""

import json
from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from contracts.v1.grading.checkresult import Points
from shared.submissionmedia import photoMediaType
from contracts.v1.events.checks import NonEmptyStr, Revision, UtcTimestamp

class EventRejected(Exception):
    """A stream entry that cannot become an event. The worker must not hand it to a handler."""

    reason: Literal["malformed", "invalid"]


class MalformedEvent(EventRejected):
    """The `data` field is not UTF-8 JSON with an object at the top level."""

    reason = "malformed"


class InvalidEvent(EventRejected):
    """The `data` field is a JSON object that does not match the checks.v1 event schema."""

    reason = "invalid"

    def __init__(self, problems: tuple[str, ...], traceId: str | None) -> None:
        self.problems = problems
        self.traceId = traceId
        super().__init__(f"checks.v1 event does not match the schema (traceId={traceId}): {'; '.join(problems)}")


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


class TaskPoints(BaseModel):
    taskNumber: NonEmptyStr
    maxScore: Points


class AssignmentPublishedInput(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    docRef: IncomingDocRef
    requirements: list[IncomingRequirement]
    subject: Literal["math", "physics"] = "math"
    taskPoints: list[TaskPoints] = Field(default_factory=list, exclude_if=lambda points: not points)

    @model_validator(mode="after")
    def uniqueMaxima(self) -> "AssignmentPublishedInput":
        if len({point.taskNumber for point in self.taskPoints}) != len(self.taskPoints):
            raise ValueError("taskPoints must have a single maximum per task")
        return self


class AttemptSubmittedInput(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Revision
    studentId: NonEmptyStr
    attemptId: NonEmptyStr
    checkId: NonEmptyStr
    docRef: IncomingDocRef

    pageRefs: list[IncomingDocRef] | None = Field(default=None, min_length=1, exclude_if=lambda refs: refs is None)

    @model_validator(mode="after")
    def verifyPages(self) -> "AttemptSubmittedInput":
        if self.pageRefs is not None:
            if self.pageRefs[0] != self.docRef or any(photoMediaType(ref.key) is None for ref in self.pageRefs):
                raise ValueError("pageRefs must contain ordered photo references starting with docRef")
        return self


def rejectBooleanVersion(value: object) -> object:
    # Python's True equals 1, so Pydantic's numeric literal alone accepts both.
    if isinstance(value, bool):
        raise ValueError("event version must be an integer")
    return value


class IncomingEnvelope(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    v: Annotated[Literal[1], BeforeValidator(rejectBooleanVersion)]
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


def describeProblems(error: ValidationError) -> tuple[str, ...]:
    """Field path and error kind for each problem, without input values: "attempt.submitted.eventId: missing"."""
    return tuple(
        f"{'.'.join(str(part) for part in item['loc']) or 'event'}: {item['type']}" for item in error.errors()
    )


def readEvent(data: str | bytes) -> IncomingEvent:
    """Turn the raw `data` field of one stream entry into a validated event.

    Raises:
        MalformedEvent: the bytes are not UTF-8, the text is not JSON, or the JSON is not an object.
        InvalidEvent: the JSON object breaks the schema; problems and traceId (when readable) go to diagnostics.
    """
    try:
        text = data.decode("utf-8") if isinstance(data, bytes) else data
        raw: object = json.loads(text)
    except UnicodeDecodeError:
        raise MalformedEvent("checks.v1 data is not valid UTF-8") from None
    except json.JSONDecodeError as error:
        raise MalformedEvent(f"checks.v1 data is not valid JSON: {error.msg} at char {error.pos}") from None
    except (ValueError, RecursionError):
        raise MalformedEvent("checks.v1 data exceeds the JSON parser limits") from None
    if not isinstance(raw, dict):
        raise MalformedEvent(f"checks.v1 data is JSON {type(raw).__name__}, expected an object")
    try:
        return incomingEventAdapter.validate_python(raw)
    except ValidationError as error:
        traceId = raw.get("traceId")
        raise InvalidEvent(describeProblems(error), traceId if isinstance(traceId, str) else None) from None
