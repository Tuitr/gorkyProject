"""Checks the model output against the taskResult schema of developer 2 before anything is stored.

Valid means: one JSON object (a single ```json fence around it is tolerated) that passes the taskResult
model of the given schema version, with every enum known and no extra fields. The model is not asked for
taskId; the gateway sets it from the request. Whether each stepId exists is checked by developer 2's worker.
"""

import json
import re

from pydantic import ValidationError

from contracts.v1.grading.taskresult import Issue, TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from references.gateway.transports.base import ModelReply, TransportError

SCHEMA_VERSION = "taskResult.v1"
KNOWN_FIELDS = frozenset(TaskResult.model_fields) | frozenset(Issue.model_fields)
FENCE = re.compile(r"\s*```(?:json)?[ \t]*\n(.*)\n[ \t]*```\s*", re.DOTALL)


class InvalidOutput(TransportError):
    """The model answered, but not with a valid taskResult. problems name fields and error types, never values."""

    def __init__(self, problems: list[str]) -> None:
        super().__init__("model_invalid_output", f"the model answer is not a valid {SCHEMA_VERSION}")
        self.problems = problems


def parseAnswer(content: str) -> dict[str, object]:
    fenced = FENCE.fullmatch(content)
    text = fenced.group(1) if fenced is not None else content
    try:
        answer = json.loads(text)
    except ValueError as error:
        raise InvalidOutput(["json: not valid JSON"]) from error
    if not isinstance(answer, dict):
        raise InvalidOutput([f"json: expected an object, got {type(answer).__name__}"])
    return answer


def describe(error: ValidationError) -> list[str]:
    """Field path and error type; a key the model invented is shown as "?" because it is model text."""

    def part(value: int | str) -> str:
        return str(value) if isinstance(value, int) or value in KNOWN_FIELDS else "?"

    return [f"{'.'.join(part(value) for value in item['loc']) or 'root'}: {item['type']}" for item in error.errors()]


def toResponse(request: ModelReviewRequest, reply: ModelReply) -> ModelReviewResponse:
    answer = parseAnswer(reply.content)
    try:
        taskResult = TaskResult.model_validate(answer | {"taskId": request.taskId})
    except ValidationError as error:
        raise InvalidOutput(describe(error)) from error
    return ModelReviewResponse(
        schemaVersion="taskResult.v1",
        taskResult=taskResult,
        modelId=reply.modelId,
        providerRequestId=reply.providerRequestId,
    )
