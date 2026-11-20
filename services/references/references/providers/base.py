"""The boundary between the registry and whatever computes a reference.

The registry owns referenceId, storage, deduplication and polling. A provider only answers
"what is the reference for this task": ready with a typed value, still pending, or refused with a reason.
The same interface will take a future solver; nothing here is specific to vector tasks.

ProviderTask is the whole author context: the revision, the cleaned statement, the constraints and the
approved family. No student work, no assignment or task ids, no closed dataset answers.
"""

from typing import Annotated, Literal, Protocol, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from contracts.v1.references.reference import VALUE_SCHEMAS, ReferenceRequest, SolverVersion

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ProviderTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentRev: Annotated[int, Field(ge=1)]
    statement: NonEmptyStr
    constraints: dict[str, JsonValue]
    familyHint: NonEmptyStr | None = None

    @classmethod
    def fromRequest(cls, request: ReferenceRequest) -> Self:
        return cls(
            assignmentRev=request.assignmentRev,
            statement=request.statement,
            constraints=dict(request.constraints),
            familyHint=request.familyHint,
        )


class ReadyOutcome(BaseModel):
    """solverVersion names the exact solver build; only a fixture may leave it empty."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["ready"] = "ready"
    source: Literal["fixture", "generated", "manual"]
    schemaId: NonEmptyStr
    value: dict[str, JsonValue]
    solverVersion: SolverVersion | None = None

    @model_validator(mode="after")
    def checkValue(self) -> Self:
        valueSchema = VALUE_SCHEMAS.get(self.schemaId)
        if valueSchema is None:
            raise ValueError(f"unknown schemaId {self.schemaId!r}")
        valueSchema.model_validate(self.value)
        if self.source != "fixture" and self.solverVersion is None:
            raise ValueError("a generated or manual reference needs solverVersion")
        return self


class PendingOutcome(BaseModel):
    """Not ready yet; the registry asks again with check() after retryAfterS."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["pending"] = "pending"
    retryAfterS: Annotated[int, Field(ge=1)]


class RefusedOutcome(BaseModel):
    """No reference: an unsupported task, or a candidate that failed independent tests."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["unsupported", "validation_failed"]
    reasonCode: NonEmptyStr


ProviderOutcome = ReadyOutcome | PendingOutcome | RefusedOutcome


class ReferenceProvider(Protocol):
    def start(self, task: ProviderTask) -> ProviderOutcome:
        """First answer for a new registry record."""
        ...

    def check(self, task: ProviderTask) -> ProviderOutcome:
        """Called once the promised wait of a pending record has passed. May be pending again."""
        ...
