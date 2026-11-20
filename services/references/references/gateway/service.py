"""POST /internal/v1/model/review: sanitize, check the journal, call the model, validate, store the answer.

An answer that is not a valid taskResult is asked for again, up to formatAttempts calls in total;
after that the caller gets 502 model_invalid_output and nothing is stored.
Every outcome is one structured log line with requestId, traceId, stage, code and duration.
"""

import logging
import time
from typing import TypedDict

from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from references.gateway.diagnostics import logEvent
from references.gateway.idempotency import Claim, ReviewJournal
from references.gateway.prompts import PromptCatalog
from references.gateway.sanitize import buildModelCall
from references.gateway.transports.base import (
    GatewayError,
    ModelCall,
    ModelTransport,
    RequestRefused,
    TransportError,
)
from references.gateway.validation import SCHEMA_VERSION, InvalidOutput, toResponse

INVALID_OUTPUT = "model_invalid_output"


class LogIds(TypedDict):
    requestId: str
    traceId: str


def elapsedMs(started: float) -> int:
    return round((time.perf_counter() - started) * 1000)


class ReviewService:
    def __init__(
        self, journal: ReviewJournal, transport: ModelTransport, prompts: PromptCatalog, formatAttempts: int = 2
    ) -> None:
        self.journal = journal
        self.transport = transport
        self.prompts = prompts
        self.formatAttempts = formatAttempts

    def review(self, request: ModelReviewRequest, traceId: str = "-") -> ModelReviewResponse:
        """409 and a stored answer come first; a refused request leaves no record."""
        started = time.perf_counter()
        ids = LogIds(requestId=request.requestId, traceId=traceId)
        try:
            stored = self.journal.stored(request)
            if stored is not None:
                logEvent("review.cached", stage="journal", code="ok", durationMs=elapsedMs(started), **ids)
                return stored
            call = buildModelCall(request, self.prompts, traceId)
            claim = self.journal.begin(request)
        except GatewayError as error:
            stage = "sanitize" if isinstance(error, RequestRefused) and error.code != "conflict" else "journal"
            self.logFailure(error, stage, started, ids)
            raise
        if isinstance(claim, ModelReviewResponse):
            logEvent("review.cached", stage="journal", code="ok", durationMs=elapsedMs(started), **ids)
            return claim
        return self.callModel(request, call, claim, started, ids)

    def callModel(
        self, request: ModelReviewRequest, call: ModelCall, claim: Claim, started: float, ids: LogIds
    ) -> ModelReviewResponse:
        attempt = 0
        while True:
            attempt += 1
            try:
                response = toResponse(request, self.transport.complete(call))
            except TransportError as error:
                if error.code != INVALID_OUTPUT:
                    self.journal.fail(claim, error.code)
                    self.logFailure(error, "transport", started, ids, attempt)
                    raise
                problems = error.problems if isinstance(error, InvalidOutput) else [error.message]
                logEvent(
                    "review.invalidOutput",
                    level=logging.WARNING,
                    stage="validate",
                    code=INVALID_OUTPUT,
                    attempt=attempt,
                    problems=problems,
                    durationMs=elapsedMs(started),
                    **ids,
                )
                if attempt < self.formatAttempts:
                    continue
                self.journal.fail(claim, INVALID_OUTPUT)
                exhausted = TransportError(
                    INVALID_OUTPUT, f"the model did not return a valid {SCHEMA_VERSION} in {attempt} attempts"
                )
                self.logFailure(exhausted, "transport", started, ids, attempt)
                raise exhausted from error
            except GatewayError as error:
                self.journal.fail(claim, error.code)
                self.logFailure(error, "transport", started, ids, attempt)
                raise
            except Exception:
                self.journal.fail(claim, "internal_error")
                logEvent(
                    "review.failed",
                    level=logging.ERROR,
                    stage="transport",
                    code="internal_error",
                    attempt=attempt,
                    durationMs=elapsedMs(started),
                    **ids,
                )
                raise
            final = self.journal.succeed(claim, response)
            logEvent(
                "review.succeeded",
                stage="review",
                code="ok",
                attempt=attempt,
                modelId=final.modelId,
                providerRequestId=final.providerRequestId,
                durationMs=elapsedMs(started),
                **ids,
            )
            return final

    def logFailure(
        self, error: GatewayError, stage: str, started: float, ids: LogIds, attempt: int | None = None
    ) -> None:
        logEvent(
            "review.failed",
            level=logging.WARNING,
            stage=stage,
            code=error.code,
            message=error.message,
            attempt=attempt,
            retryAfterS=error.retryAfterS,
            durationMs=elapsedMs(started),
            **ids,
        )
