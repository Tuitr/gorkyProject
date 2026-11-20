"""Prepare a single mathematical review and bind the returned issues to verified steps."""

import hashlib
import json
import os
import re
from collections import defaultdict
from collections.abc import Callable, Mapping
from http.client import HTTPConnection, HTTPSConnection
from urllib.parse import urlsplit

from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse, ReviewReference, ReviewStep
from contracts.v1.references.reference import ReferenceReady, ReferenceResult
from grading.assignment import AssignmentTask
from grading.submission import SubmissionReading

PROMPT_VERSION = "review.v1"
DOCUMENT_LABEL = re.compile(
    r"\b(?:фио|имя ученика|ученик|ученица|выполнил[аи]?|подпись|автор|имя файла|файл)\s*:",
    re.IGNORECASE,
)


class DocumentMetadataInReview(ValueError):
    """A source line still contains a labelled signature or document metadata."""


def buildReviewRequest(checkId: str, task: AssignmentTask, steps: list[ReviewStep], reference: ReferenceReady,
                       promptVersion: str = PROMPT_VERSION) -> ModelReviewRequest:
    if any(DOCUMENT_LABEL.search(text) for text in (task.statement, *task.requirements, *(s.markdown for s in steps))):
        raise DocumentMetadataInReview("review content contains a document metadata field")
    body = ModelReviewRequest(
        requestId="pending", promptVersion=promptVersion, taskId=task.taskId, statement=task.statement,
        rubric=list(task.requirements), steps=steps,
        reference=ReviewReference(referenceId=reference.referenceId, schemaId=reference.schemaId, value=reference.value),
    )
    identity = [checkId, body.model_dump(mode="json", exclude={"requestId"})]
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())
    return body.model_copy(update={"requestId": digest.hexdigest()})


def sendReview(request: ModelReviewRequest) -> ModelReviewResponse:
    url = urlsplit(os.environ["GRADING_GATEWAY_URL"])
    if url.scheme not in ("http", "https") or not url.hostname or url.username is not None or url.query or url.fragment:
        raise ValueError("expected an HTTP(S) base URL for the model gateway")
    connection = (HTTPSConnection if url.scheme == "https" else HTTPConnection)(url.hostname, url.port, timeout=60)
    try:
        connection.request("POST", url.path.rstrip("/") + "/internal/v1/model/review",
                           body=request.model_dump_json().encode(),
                           headers={"Content-Type": "application/json", "Authorization": "Bearer " + os.environ["GRADING_GATEWAY_TOKEN"]})
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError(f"model gateway returned HTTP {response.status}")
        return ModelReviewResponse.model_validate_json(response.read())
    finally:
        connection.close()


def localResult(taskId: str, *, skipped: bool, required: bool) -> TaskResult:
    return TaskResult(taskId=taskId, answer="skipped" if skipped else "unreadable",
                      reasoning="not_shown" if skipped else "unknown", method="unconfirmed" if required else "optional",
                      issues=[], needsReview=not skipped)


def reviewTasks(checkId: str, tasks: tuple[AssignmentTask, ...], reading: SubmissionReading,
                references: Mapping[str, ReferenceResult], transport: Callable[[ModelReviewRequest], ModelReviewResponse] = sendReview,
                promptVersion: str = PROMPT_VERSION) -> tuple[TaskResult, ...]:
    taskReadings = {item.taskId: item for item in reading.taskReadings}
    steps: dict[str, list[ReviewStep]] = defaultdict(list)
    for step in reading.steps:
        if step.readStatus == "read":
            steps[step.taskId].append(ReviewStep(stepId=step.stepId, markdown=step.markdown))
    results = []
    for task in tasks:
        item = taskReadings[task.taskId]
        reference = references.get(task.taskId)
        if item.status in ("skipped", "needsReview") or not isinstance(reference, ReferenceReady) or not steps[task.taskId]:
            results.append(localResult(task.taskId, skipped=item.status == "skipped", required=bool(task.requirements)))
            continue
        try:
            request = buildReviewRequest(checkId, task, steps[task.taskId], reference, promptVersion)
        except DocumentMetadataInReview:
            results.append(localResult(task.taskId, skipped=False, required=bool(task.requirements)))
            continue
        result = transport(request).taskResult
        if result.taskId != task.taskId:
            result = localResult(task.taskId, skipped=False, required=bool(task.requirements))
        results.append(result)
    return tuple(results)
