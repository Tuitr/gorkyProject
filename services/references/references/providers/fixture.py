"""Deterministic reference provider for this iteration: answers only registered fixtures.

A fixture matches when the cleaned statement and the constraints are exactly equal (constraint key
order does not matter). Nothing is computed and nothing is taken from student work: an unknown or
changed statement is unsupported. Real solvers replace this provider after task families are chosen.
A fixture with pendingForS > 0 answers pending first and its final outcome on check().
"""

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from contracts.v1.references.reference import VALUE_SCHEMAS
from references.providers.base import PendingOutcome, ProviderOutcome, ProviderTask, ReadyOutcome, RefusedOutcome

UNKNOWN_TASK = "unknown_task"

DEFAULT_FIXTURES_PATH = Path(__file__).parents[1] / "fixtures" / "references.json"

NonEmptyStr = Annotated[str, Field(min_length=1)]


class FixtureOutcome(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["ready", "validation_failed"]
    schemaId: NonEmptyStr | None = None
    value: dict[str, JsonValue] | None = None
    reasonCode: NonEmptyStr | None = None

    @model_validator(mode="after")
    def checkOutcome(self) -> Self:
        if self.state == "ready":
            if self.schemaId is None or self.value is None or self.reasonCode is not None:
                raise ValueError("a ready fixture needs schemaId and value and no reasonCode")
            valueSchema = VALUE_SCHEMAS.get(self.schemaId)
            if valueSchema is None:
                raise ValueError(f"unknown schemaId {self.schemaId!r}")
            valueSchema.model_validate(self.value)
        elif self.reasonCode is None or self.value is not None:
            raise ValueError("a failed fixture needs reasonCode and no value")
        return self

    def toOutcome(self) -> ReadyOutcome | RefusedOutcome:
        if self.state == "ready":
            assert self.schemaId is not None and self.value is not None
            return ReadyOutcome(source="fixture", schemaId=self.schemaId, value=self.value, solverVersion=None)
        assert self.reasonCode is not None
        return RefusedOutcome(state=self.state, reasonCode=self.reasonCode)


class ReferenceFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixtureId: NonEmptyStr
    statement: NonEmptyStr
    constraints: dict[str, JsonValue]
    pendingForS: Annotated[int, Field(ge=0)] = 0
    outcome: FixtureOutcome


class FixtureProvider:
    def __init__(self, fixtures: list[ReferenceFixture]) -> None:
        ids = [fixture.fixtureId for fixture in fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("fixtureId values must be unique")
        self.fixtures = fixtures

    @classmethod
    def fromFile(cls, path: Path = DEFAULT_FIXTURES_PATH) -> Self:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([ReferenceFixture.model_validate(entry) for entry in raw])

    def find(self, task: ProviderTask) -> ReferenceFixture | None:
        for fixture in self.fixtures:
            if fixture.statement == task.statement and fixture.constraints == task.constraints:
                return fixture
        return None

    def start(self, task: ProviderTask) -> ProviderOutcome:
        fixture = self.find(task)
        if fixture is None:
            return RefusedOutcome(state="unsupported", reasonCode=UNKNOWN_TASK)
        if fixture.pendingForS > 0:
            return PendingOutcome(retryAfterS=fixture.pendingForS)
        return fixture.outcome.toOutcome()

    def check(self, task: ProviderTask) -> ProviderOutcome:
        fixture = self.find(task)
        if fixture is None:
            return RefusedOutcome(state="unsupported", reasonCode=UNKNOWN_TASK)
        return fixture.outcome.toOutcome()
