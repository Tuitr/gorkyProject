"""The one call the gateway makes to a model, and what comes back.

A transport only moves text: it does not build prompts, judge answers or validate taskResult.
The prompt text comes from developer 2 (promptVersion); taskResult validation is C4.4.
"""

from typing import Annotated, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, JsonValue

from contracts.v1.common.errors import RETRYABLE_CODES
from contracts.v1.references.modelreview import ReviewStep

NonEmptyStr = Annotated[str, Field(min_length=1)]


class ProviderReference(BaseModel):
    """The reference value without its registry referenceId: the provider does not need our IDs."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schemaId: NonEmptyStr
    value: dict[str, JsonValue]


class ReviewData(BaseModel):
    """Everything a provider may see: mathematical content and the anchor stepIds."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    statement: NonEmptyStr
    rubric: tuple[NonEmptyStr, ...]
    steps: Annotated[tuple[ReviewStep, ...], Field(min_length=1)]
    reference: ProviderReference


class ModelCall(BaseModel):
    """requestId, promptVersion and taskId are for tracing and fixture lookup; only instructions and data go out."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    requestId: NonEmptyStr
    promptVersion: NonEmptyStr
    taskId: NonEmptyStr
    instructions: NonEmptyStr
    data: ReviewData


class ModelReply(BaseModel):
    """Raw model output. content is expected to be taskResult JSON, but nothing here checks it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    content: str
    modelId: NonEmptyStr
    providerRequestId: NonEmptyStr | None = None


class ConfigError(ValueError):
    """The environment does not choose a usable transport. Raised at startup, never per request."""


class TransportError(Exception):
    """A failed call, with an ApiError code from contracts v1. The message never holds request text or secrets."""

    def __init__(self, code: str, message: str, retryAfterS: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryAfterS = retryAfterS

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE_CODES


TransportName = Literal["fixture", "gigachat"]


class ModelTransport(Protocol):
    @property
    def name(self) -> TransportName: ...

    def complete(self, call: ModelCall) -> ModelReply: ...
