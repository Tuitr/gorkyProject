"""Deterministic transport for tests and the test stand: recorded replies, never a network call.

A reply matches when taskId, statement and the steps (stepId and markdown, in order) are all equal,
so two students of one task get their own replies. A missing record is a local error:
this transport has no way to reach a paid API.

A fixture may also script provider failures (C5.3): the first calls of each requestId raise them in order,
then the recorded content comes back. Without content the last failure repeats on every later call.
Counting is per requestId inside this transport object, so the sequence never depends on timers,
other requests or test order. Failure fixtures live in their own file, loaded only in test configuration.

Solver author calls (references/solvers) match an AuthorFixture by statement, approach and whether the call
repeats a failed attempt; they live in fixtures/solverAuthors.json.
"""

import json
from collections import Counter
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from contracts.v1.references.modelreview import ReviewStep
from references.gateway.transports.base import (
    AuthorApproach,
    AuthorData,
    ModelCall,
    ModelReply,
    NonEmptyStr,
    ReviewData,
    TransportError,
)

FIXTURES_DIR = Path(__file__).parents[2] / "fixtures"
DEFAULT_REVIEWS_PATH = FIXTURES_DIR / "reviews.json"
FAILURE_SCENARIOS_PATH = FIXTURES_DIR / "reviewFailures.json"
DEFAULT_AUTHORS_PATH = FIXTURES_DIR / "solverAuthors.json"
FIXTURE_MODEL_ID = "fixture"
NO_FIXTURE = "internal_error"


class FixtureFailure(BaseModel):
    """What a real transport raises for a 429, a 503, a provider timeout or a broken call."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    code: Literal["rate_limited", "unavailable", "timeout", "internal_error"]
    retryAfterS: Annotated[int, Field(ge=1)] | None = None

    def toError(self, fixtureId: str) -> TransportError:
        return TransportError(
            self.code, f"simulated {self.code} from fixture {fixtureId!r}; no external call was made", self.retryAfterS
        )


class ReviewFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixtureId: NonEmptyStr
    taskId: NonEmptyStr
    statement: NonEmptyStr
    steps: Annotated[tuple[ReviewStep, ...], Field(min_length=1)]
    failures: tuple[FixtureFailure, ...] = ()
    """Raised on calls 1..len(failures) of each requestId, before content."""
    content: dict[str, JsonValue] | str | None = None
    """The model output: a JSON object (sent as JSON text) or raw text, e.g. broken JSON."""
    modelId: NonEmptyStr = FIXTURE_MODEL_ID
    providerRequestId: NonEmptyStr | None = None

    @model_validator(mode="after")
    def checkOutcome(self) -> Self:
        if self.content is None and not self.failures:
            raise ValueError("a review fixture needs content, failures or both")
        return self

    def matches(self, call: ModelCall) -> bool:
        return (
            isinstance(call.data, ReviewData)
            and self.taskId == call.taskId
            and self.statement == call.data.statement
            and self.steps == call.data.steps
        )

    def answer(self, callNo: int) -> ModelReply:
        """callNo counts from 1 for each requestId."""
        if callNo <= len(self.failures):
            raise self.failures[callNo - 1].toError(self.fixtureId)
        if self.content is None:
            raise self.failures[-1].toError(self.fixtureId)
        content = self.content if isinstance(self.content, str) else json.dumps(self.content, ensure_ascii=False)
        return ModelReply(content=content, modelId=self.modelId, providerRequestId=self.providerRequestId)


class AuthorFixture(BaseModel):
    """A recorded answer of the solver author: the code it wrote for one statement and approach."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    fixtureId: NonEmptyStr
    statement: NonEmptyStr
    approach: AuthorApproach
    retry: bool = False
    """False answers the first attempt, True a repeat after a failure in the sandbox."""
    content: dict[str, JsonValue] | str
    modelId: NonEmptyStr = FIXTURE_MODEL_ID

    def matches(self, call: ModelCall) -> bool:
        data = call.data
        return (
            isinstance(data, AuthorData)
            and self.statement == data.statement
            and self.approach == data.approach
            and self.retry == (data.previousError is not None)
        )

    def answer(self) -> ModelReply:
        content = self.content if isinstance(self.content, str) else json.dumps(self.content, ensure_ascii=False)
        return ModelReply(content=content, modelId=self.modelId)


class FixtureTransport:
    name: Literal["fixture"] = "fixture"

    def __init__(self, fixtures: list[ReviewFixture], authors: list[AuthorFixture] | None = None) -> None:
        self.authors = authors if authors is not None else []
        ids = [fixture.fixtureId for fixture in fixtures] + [author.fixtureId for author in self.authors]
        if len(ids) != len(set(ids)):
            raise ValueError("fixtureId values must be unique")
        self.fixtures = fixtures
        self.calls: list[ModelCall] = []
        self.callsPerRequest: Counter[tuple[str, str]] = Counter()

    @classmethod
    def fromFile(cls, *paths: Path, authorsPath: Path | None = DEFAULT_AUTHORS_PATH) -> Self:
        """One or more review fixture files and the author fixtures; fixtureId must be unique across all of them."""
        entries = [entry for path in paths or (DEFAULT_REVIEWS_PATH,) for entry in readEntries(path)]
        authors = [] if authorsPath is None else [AuthorFixture.model_validate(e) for e in readEntries(authorsPath)]
        return cls([ReviewFixture.model_validate(entry) for entry in entries], authors)

    def complete(self, call: ModelCall) -> ModelReply:
        self.calls.append(call)
        if isinstance(call.data, AuthorData):
            for author in self.authors:
                if author.matches(call):
                    return author.answer()
            raise TransportError(
                NO_FIXTURE,
                f"fixture transport has no recorded solver for approach {call.data.approach!r} "
                f"(requestId {call.requestId!r}); no external call was made",
            )
        for fixture in self.fixtures:
            if fixture.matches(call):
                key = (fixture.fixtureId, call.requestId)
                self.callsPerRequest[key] += 1
                return fixture.answer(self.callsPerRequest[key])
        raise TransportError(
            NO_FIXTURE,
            f"fixture transport has no recorded reply for task {call.taskId!r} "
            f"(requestId {call.requestId!r}); no external call was made",
        )


def readEntries(path: Path) -> list[JsonValue]:
    entries = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        raise ValueError(f"{path.name} must hold a JSON list of review fixtures")
    return entries
