"""POST /internal/v1/references and GET /internal/v1/references/{referenceId}.

Owner: developer 3. Consumer: developer 2 (grading worker).

POST answers 200 with a final state (ready, unsupported, validation_failed) or 202 with pending.
GET returns the stored state of the same referenceId until it is final.
"""

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, TypeAdapter, model_validator

NonEmptyStr = Annotated[str, Field(min_length=1)]
FamilyId = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]{0,63}$")]
"""An approved task family, camelCase, for example "vectorLength"."""
SolverVersion = Annotated[str, Field(pattern=r"^[a-z][A-Za-z0-9]{0,63}\.v[1-9][0-9]{0,8}$")]
"""One immutable solver build: "<familyId>.v<N>", for example "vectorLength.v2". There is no "latest"."""

ReferenceState = Literal["ready", "pending", "unsupported", "validation_failed"]
ReferenceSource = Literal["fixture", "generated", "manual"]


class ReferenceRequest(BaseModel):
    """Cleaned task statement and the assignment revision it belongs to."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assignmentId: NonEmptyStr
    assignmentRev: Annotated[int, Field(ge=1)]
    taskId: NonEmptyStr
    statement: NonEmptyStr
    constraints: dict[str, JsonValue]
    familyHint: NonEmptyStr | None = None


class ScalarValueV1(BaseModel):
    """schemaId=scalar.v1: one exact answer written as a string, for example "5" or "-3/4"."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    exact: NonEmptyStr


VALUE_SCHEMAS: dict[str, type[BaseModel]] = {"scalar.v1": ScalarValueV1}
"""Every schemaId a ready reference may carry. A new task family registers its value schema here."""


class ReferenceReady(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    referenceId: NonEmptyStr
    state: Literal["ready"]
    source: ReferenceSource
    schemaId: NonEmptyStr
    value: dict[str, JsonValue]
    solverVersion: SolverVersion | None
    """The exact solver build that computed the value (see solverartifact.py); null only for a fixture."""

    @model_validator(mode="after")
    def checkValueAndVersion(self) -> Self:
        valueSchema = VALUE_SCHEMAS.get(self.schemaId)
        if valueSchema is None:
            raise ValueError(f"unknown schemaId {self.schemaId!r}")
        valueSchema.model_validate(self.value)
        if self.source != "fixture" and self.solverVersion is None:
            raise ValueError("solverVersion is required unless source is fixture")
        return self


class ReferencePending(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    referenceId: NonEmptyStr
    state: Literal["pending"]
    retryAfterS: Annotated[int, Field(ge=0)]


class ReferenceUnsupported(BaseModel):
    """Final: no reference can be produced for this task. The task goes to the teacher."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    referenceId: NonEmptyStr
    state: Literal["unsupported"]
    reasonCode: NonEmptyStr


class ReferenceValidationFailed(BaseModel):
    """Final: a candidate reference failed its checks. The task goes to the teacher."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    referenceId: NonEmptyStr
    state: Literal["validation_failed"]
    reasonCode: NonEmptyStr


ReferenceResult = Annotated[
    ReferenceReady | ReferencePending | ReferenceUnsupported | ReferenceValidationFailed,
    Field(discriminator="state"),
]
referenceResultAdapter: TypeAdapter[ReferenceResult] = TypeAdapter(ReferenceResult)
