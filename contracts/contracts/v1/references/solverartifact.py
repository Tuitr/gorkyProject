"""Metadata of one published solver version: what it solves, where its code and tests are, how it was made.

Owner: developer 3. Written by solver-builder (services/references/references/solvers) when REFERENCES_PROVIDER is
generating; fixtures have no artifact (source=fixture, solverVersion=null).

Writes: only the reference service (developer 3), once, when a candidate has passed its independent cross-check.
The metadata object and the objects it points to are never overwritten; a fix is a new solverVersion.
Reads: the reference service, to compute a reference and to explain an old check by its solverVersion.
Developer 2 sees only solverVersion in ReferenceReady and never reads these objects in S3.

S3 layout, all under the prefix that developer 3 alone may read and write:
    references/solvers/<solverVersion>/metadata.json   this document
    references/solvers/<solverVersion>/<name>          code and tests, each with its sha256
There is no "latest" key. Which version serves new requests is a registry decision kept in PostgreSQL;
every registry record keeps the solverVersion it was computed with, so an old check still finds its exact build.
"""

import re
from typing import Annotated, Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from contracts.v1.references.reference import VALUE_SCHEMAS, FamilyId, SolverVersion

SOLVERS_PREFIX = "references/solvers"
Sha256Hex = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
OBJECT_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*(\.[A-Za-z0-9_-]+)*")


def familyOf(solverVersion: str) -> str:
    return solverVersion.rsplit(".v", 1)[0]


def versionPrefix(solverVersion: str) -> str:
    return f"{SOLVERS_PREFIX}/{solverVersion}/"


def metadataKey(solverVersion: str) -> str:
    return versionPrefix(solverVersion) + "metadata.json"


class StoredObject(BaseModel):
    """One immutable object in S3. The hash lets a reader prove it got the bytes that were tested."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    key: Annotated[str, Field(min_length=1, max_length=512)]
    sha256: Sha256Hex
    sizeBytes: Annotated[int, Field(ge=1)]


class SolverScope(BaseModel):
    """Which requests the version may answer: one approved family, one value schema, listed constraint values."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    familyId: FamilyId
    schemaId: Annotated[str, Field(min_length=1)]
    constraints: dict[str, list[JsonValue]]
    """Accepted values per constraint. A request with any other constraint or value is out of scope."""

    @model_validator(mode="after")
    def checkSchema(self) -> Self:
        if self.schemaId not in VALUE_SCHEMAS:
            raise ValueError(f"unknown schemaId {self.schemaId!r}")
        return self

    def covers(self, familyId: str | None, constraints: dict[str, JsonValue]) -> bool:
        if familyId != self.familyId:
            return False
        return all(name in self.constraints and value in self.constraints[name] for name, value in constraints.items())


class GenerationTrace(BaseModel):
    """How a generated version was made, for debugging. Hashes only: no statement text, no student work."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    modelId: Annotated[str, Field(min_length=1)]
    authorInputSha256: Sha256Hex
    """Hash of the author context (family input and output description and the statement) the model was given."""


class SolverArtifact(BaseModel):
    """schemaVersion=solverArtifact.v1. A fixture has no artifact: it is not a solver build."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaVersion: Literal["solverArtifact.v1"]
    solverVersion: SolverVersion
    source: Literal["generated", "manual"]
    scope: SolverScope
    code: StoredObject
    tests: StoredObject
    """The independent closed tests the version passed; never shown to the author."""
    publishedAt: AwareDatetime
    generation: GenerationTrace | None
    """Required for source=generated, absent for manual."""

    @model_validator(mode="after")
    def checkConsistency(self) -> Self:
        if familyOf(self.solverVersion) != self.scope.familyId:
            raise ValueError(f"solverVersion {self.solverVersion!r} is not a version of family {self.scope.familyId!r}")
        prefix = versionPrefix(self.solverVersion)
        for field, stored in (("code", self.code), ("tests", self.tests)):
            name = stored.key.removeprefix(prefix)
            if not stored.key.startswith(prefix) or not OBJECT_NAME.fullmatch(name) or name == "metadata.json":
                raise ValueError(f"{field}.key must name a file directly under {prefix}")
        if self.code.key == self.tests.key:
            raise ValueError("code and tests must be different objects")
        if (self.source == "generated") != (self.generation is not None):
            raise ValueError("generation is required for source=generated and absent for source=manual")
        return self
