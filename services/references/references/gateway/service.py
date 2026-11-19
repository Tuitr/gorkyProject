"""POST /internal/v1/model/review: sanitize, check the journal, call the model once, store the answer."""

import json

from pydantic import ValidationError

from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from references.gateway.idempotency import ReviewJournal
from references.gateway.prompts import PromptCatalog
from references.gateway.sanitize import buildModelCall
from references.gateway.transports.base import GatewayError, ModelReply, ModelTransport, TransportError


def toResponse(request: ModelReviewRequest, reply: ModelReply) -> ModelReviewResponse:
    """The model answers without taskId; the gateway adds it. Retries and diagnostics for bad output: C4.4."""
    try:
        answer = json.loads(reply.content)
        if not isinstance(answer, dict):
            raise ValueError("not a JSON object")
        taskResult = TaskResult.model_validate(answer | {"taskId": request.taskId})
    except (ValueError, ValidationError) as error:
        raise TransportError("model_invalid_output", "the model answer is not a valid taskResult") from error
    return ModelReviewResponse(
        schemaVersion="taskResult.v1",
        taskResult=taskResult,
        modelId=reply.modelId,
        providerRequestId=reply.providerRequestId,
    )


class ReviewService:
    def __init__(self, journal: ReviewJournal, transport: ModelTransport, prompts: PromptCatalog) -> None:
        self.journal = journal
        self.transport = transport
        self.prompts = prompts

    def review(self, request: ModelReviewRequest) -> ModelReviewResponse:
        """409 and a stored answer come first; a refused request leaves no record."""
        stored = self.journal.stored(request)
        if stored is not None:
            return stored
        call = buildModelCall(request, self.prompts)
        claim = self.journal.begin(request)
        if isinstance(claim, ModelReviewResponse):
            return claim
        try:
            response = toResponse(request, self.transport.complete(call))
        except GatewayError as error:
            self.journal.fail(claim, error.code)
            raise
        except Exception:
            self.journal.fail(claim, "internal_error")
            raise
        return self.journal.succeed(claim, response)
