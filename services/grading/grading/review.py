"""Prepare a single mathematical review and bind the returned issues to verified steps."""

import hashlib
import json
import os
import re
from collections import Counter, defaultdict, deque
from collections.abc import Callable, Mapping
from http.client import HTTPConnection, HTTPSConnection
from typing import Literal
from urllib.parse import urlsplit

from contracts.v1.grading.taskresult import Issue, TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse, ReviewReference, ReviewStep
from contracts.v1.references.reference import ReferenceReady, ReferenceResult
from grading.assignment import AssignmentTask, subjectOf
from grading.submission import SubmissionReading, TaskReading

PROMPT_VERSION = "review.v2"
PARTIAL_REVIEW_PROMPT = "reviewPartial.v1"
SUBJECT_PROMPT_VERSIONS = {"physics": "review.v3"}
"""A subject with its own review prompt; math uses the promptVersion the caller passes (review.v2 by default)."""
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


def anchoredIssues(issues: list[Issue], stepIds: set[str]) -> list[Issue]:
    counts = Counter(issue.issueId for issue in issues)
    children: dict[str, list[str]] = defaultdict(list)
    roots: deque[str] = deque()
    for issue in issues:
        if counts[issue.issueId] != 1 or issue.stepId not in stepIds:
            continue
        if issue.causedByIssueId is None:
            roots.append(issue.issueId)
        else:
            children[issue.causedByIssueId].append(issue.issueId)
    # Only chains reachable from an unambiguous, anchored root are safe to publish.
    accepted = set()
    while roots:
        issueId = roots.popleft()
        accepted.add(issueId)
        roots.extend(children[issueId])
    return [issue for issue in issues if issue.issueId in accepted]


def validatedResult(request: ModelReviewRequest, result: TaskResult, reading: TaskReading) -> TaskResult:
    if result.taskId != request.taskId:
        return localResult(request.taskId, required=bool(request.rubric))
    issues = anchoredIssues(result.issues, {step.stepId for step in request.steps})
    partial = reading.status == "partialCrossout"
    reasoning = "not_shown" if reading.status == "answerOnly" else result.reasoning
    method = result.method if request.rubric else "optional"
    if reading.status == "answerOnly" and request.rubric:
        method = "unconfirmed"
    return result.model_copy(update={
        "answer": "unreadable" if partial else result.answer, "issues": issues, "reasoning": reasoning, "method": method,
        "needsReview": partial or result.needsReview or result.answer == "unreadable" or len(issues) != len(result.issues),
    })


def localResult(taskId: str, *, required: bool, answer: Literal["skipped", "incorrect", "unreadable"] = "unreadable") -> TaskResult:
    return TaskResult(taskId=taskId, answer=answer,
                      reasoning="unknown" if answer == "unreadable" else "not_shown",
                      method="unconfirmed" if required else "optional", issues=[], needsReview=answer == "unreadable")


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
        if item.status == "omitted":
            results.append(localResult(task.taskId, required=bool(task.requirements), answer="incorrect"))
            continue
        reference = references.get(task.taskId)
        if item.status in ("skipped", "needsReview") or not isinstance(reference, ReferenceReady) or not steps[task.taskId]:
            results.append(localResult(task.taskId, required=bool(task.requirements),
                                       answer="skipped" if item.status == "skipped" else "unreadable"))
            continue
        try:
            taskPrompt = (PARTIAL_REVIEW_PROMPT if item.status == "partialCrossout"
                          else SUBJECT_PROMPT_VERSIONS.get(subjectOf(task), promptVersion))
            request = buildReviewRequest(checkId, task, steps[task.taskId], reference, taskPrompt)
        except DocumentMetadataInReview:
            results.append(localResult(task.taskId, required=bool(task.requirements)))
            continue
        verdict = item.exactVerdict(reference)
        try:
            checked = validatedResult(request, transport(request).taskResult, item)
        except (OSError, RuntimeError):
            if verdict is None:
                raise
            checked = localResult(task.taskId, required=False)
        if verdict is not None:
            checked = checked.model_copy(update={"answer": verdict, "needsReview": False})
        results.append(checked)
    return tuple(results)
