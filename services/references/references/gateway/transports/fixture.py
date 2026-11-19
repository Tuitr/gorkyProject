"""Deterministic transport for tests and the test stand: recorded replies, never a network call.

A reply matches when taskId, statement and the steps (stepId and markdown, in order) are all equal,
so two students of one task get their own replies. A missing record is a local error:
this transport has no way to reach a paid API.
"""

import json
from pathlib import Path
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from contracts.v1.references.modelreview import ReviewStep
from references.gateway.transports.base import ModelCall, ModelReply, NonEmptyStr, TransportError

DEFAULT_REVIEWS_PATH = Path(__file__).parents[2] / "fixtures" / "reviews.json"
FIXTURE_MODEL_ID = "fixture"
NO_FIXTURE = "internal_error"


class ReviewFixture(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    fixtureId: NonEmptyStr
    taskId: NonEmptyStr
    statement: NonEmptyStr
    steps: Annotated[tuple[ReviewStep, ...], Field(min_length=1)]
    content: dict[str, JsonValue] | str
    """The model output: a JSON object (sent as JSON text) or raw text, e.g. broken JSON."""
    modelId: NonEmptyStr = FIXTURE_MODEL_ID
    providerRequestId: NonEmptyStr | None = None

    def matches(self, call: ModelCall) -> bool:
        return (
            self.taskId == call.taskId and self.statement == call.data.statement and self.steps == call.data.steps
        )

    def reply(self) -> ModelReply:
        content = self.content if isinstance(self.content, str) else json.dumps(self.content, ensure_ascii=False)
        return ModelReply(content=content, modelId=self.modelId, providerRequestId=self.providerRequestId)


class FixtureTransport:
    name: Literal["fixture"] = "fixture"

    def __init__(self, fixtures: list[ReviewFixture]) -> None:
        ids = [fixture.fixtureId for fixture in fixtures]
        if len(ids) != len(set(ids)):
            raise ValueError("fixtureId values must be unique")
        self.fixtures = fixtures
        self.calls: list[ModelCall] = []

    @classmethod
    def fromFile(cls, path: Path = DEFAULT_REVIEWS_PATH) -> Self:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return cls([ReviewFixture.model_validate(entry) for entry in raw])

    def complete(self, call: ModelCall) -> ModelReply:
        self.calls.append(call)
        for fixture in self.fixtures:
            if fixture.matches(call):
                return fixture.reply()
        raise TransportError(
            NO_FIXTURE,
            f"fixture transport has no recorded reply for task {call.taskId!r} "
            f"(requestId {call.requestId!r}); no external call was made",
        )
