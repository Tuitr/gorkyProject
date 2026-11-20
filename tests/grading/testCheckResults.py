"""Persist a check through a worker retry and publish it with its S3 artifact."""

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from contextlib import closing
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import boto3
import pytest
from botocore.client import Config
from botocore.stub import Stubber
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine

from contracts.v1.events.checks import DATA_FIELD
from contracts.v1.grading.checkresult import PublishedCheck
from contracts.v1.grading.taskresult import Issue, TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from contracts.v1.references.reference import ReferenceReady
from dataset.docxwriter import Paragraph, buildDocx
from grading.main import createApp
from grading.markdown import inlineText
from grading.markdownStorage import MarkdownStorage
from grading.results import ArtifactUnavailable, ResultConflict
from grading.settings import Settings
from grading.stream import StreamConsumer
from grading.worker import GradingWorker
from shared.clock import ManualClock


@pytest.fixture
def resultStorage() -> Iterator[tuple[MarkdownStorage, str]]:
    admin = boto3.client(
        "s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:18333"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "gorky-dev"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "gorky-dev-secret"),
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(proxies={}, s3={"addressing_style": "path"}),
    )
    if not any(bucket.get("Name") == "results" for bucket in admin.list_buckets()["Buckets"]):
        admin.create_bucket(Bucket="results")
    checkId = f"c-{uuid4().hex}"
    storage = MarkdownStorage.fromEnv()
    try:
        yield storage, checkId
    finally:
        checkKey = hashlib.sha256(checkId.encode()).hexdigest()
        for rev in (1, 2):
            admin.delete_object(Bucket="results", Key=f"checks/{checkKey}/{rev}.md")
        storage.client.close()
        admin.close()


def testRecoversDraftAndReadsPublishedRevisions(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], resultStorage: tuple[MarkdownStorage, str],
    contractExample: Callable[[str], Any],
) -> None:
    stream, streamName = gradingStream
    clock = ManualClock()
    worker = GradingWorker(StreamConsumer(stream, "initial", streamName=streamName), gradingEngine, clock=clock.now)
    assignment = buildDocx("Вариант", [Paragraph("1. Найдите 2² + 1.")])
    submission = buildDocx("Работа", [Paragraph("Задание 1. 2² + 1 = 4"), Paragraph("Ответ: 4")])
    published = contractExample("events/assignmentPublished.json")
    published["payload"]["requirements"] = []
    published["payload"]["docRef"]["sha256"] = hashlib.sha256(assignment).hexdigest()
    submitted = contractExample("events/attemptSubmitted.json")
    storage, checkId = resultStorage
    submitted["payload"]["checkId"] = checkId
    submitted["payload"]["docRef"]["sha256"] = hashlib.sha256(submission).hexdigest()
    for event in (published, submitted):
        stream.xadd(streamName, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    assignmentLease = worker.jobs.claimAssignment()
    assert assignmentLease is not None
    reference = ReferenceReady(referenceId="r1", state="ready", source="fixture", schemaId="scalar.v1",
                               value={"exact": "5"}, solverVersion=None)
    worker.prepareAssignment(assignmentLease, lambda _: assignment, requestReference=lambda _: reference)

    settings = Settings(serviceTokensJson=json.dumps({"test": {"serviceName": "cabinet", "rights": ["checks.read"]}}))
    api = TestClient(createApp(settings, engine=gradingEngine, storage=storage))
    url = f"/internal/v1/checks/{checkId}"
    headers = {"Authorization": "Bearer test"}
    assert api.get(url).status_code == 401
    assert api.get(url, headers=headers).json()["state"] == "queued"
    lease = worker.jobs.claimCheck()
    assert lease is not None
    issue = Issue(issueId="i1", stepId="s1", category="arithmetic", comment="Вычислите сумму.",
                  correction="$2^2 + 1 = 5$")
    result = TaskResult(taskId="t1", answer="incorrect", reasoning="invalid", method="optional",
                        issues=[issue], needsReview=False)

    def assess(request: ModelReviewRequest) -> ModelReviewResponse:
        assert request.taskId == "t1"
        return ModelReviewResponse(schemaVersion="taskResult.v1", taskResult=result, modelId="fixture")

    with Stubber(storage.client) as s3Failure:
        s3Failure.add_client_error("put_object", service_error_code="ServiceUnavailable", http_status_code=503)
        with pytest.raises(ArtifactUnavailable):
            worker.completeCheck(lease, lambda _: submission, storage, transport=assess)
        s3Failure.assert_no_pending_responses()
    draft = worker.results.findDraft(checkId, "initial")
    assert draft is not None and draft.resultRev == 1 and draft.taskResults == [result]
    assert "Ответ отличается от эталона." in draft.overallReview
    assert api.get(url, headers=headers).json() == {"checkId": checkId, "state": "running", "reason": "awaiting_artifact"}

    clock.advance(61)
    recovered = GradingWorker(StreamConsumer(stream, "recovered", streamName=streamName), gradingEngine, clock=clock.now)
    retry = recovered.jobs.claimCheck()
    assert retry is not None
    def resume() -> PublishedCheck | None:
        return recovered.completeCheck(
            retry, lambda _: pytest.fail("draft already calculated"), storage,
            transport=lambda _: pytest.fail("review already completed"),
        )

    with patch.object(recovered.results, "publish", side_effect=RuntimeError("database did not commit")):
        with pytest.raises(RuntimeError, match="database did not commit"):
            resume()
    assert api.get(url, headers=headers).json()["reason"] == "awaiting_artifact"
    first = resume()
    assert first is not None and first.state == "ready" and first.attemptId == "a1"
    firstRef = first.markdownRef
    loaded = storage.client.get_object(Bucket="results", Key=firstRef.key)
    with closing(loaded["Body"]) as streamBody:
        report = streamBody.read().decode("utf-8")
    assert loaded["ContentDisposition"] == 'attachment; filename="review-1.md"'
    assert "## Общий отзыв" in report and "Ответ отличается от эталона" in report
    assert "### Шаг s1" in report and "$2^2 + 1 = 4$" in report
    assert "Вычислите сумму" in report and "$2^2 + 1 = 5$" in report
    assert resume() == first
    with pytest.raises(ArtifactUnavailable, match="bytes differ"):
        storage.save(checkId, 1, "Этот текст нельзя положить по ключу опубликованного отчёта.")
    assert api.get(url, headers=headers).json() == first.model_dump(mode="json")
    with pytest.raises(ResultConflict):
        recovered.results.saveDraft(checkId, "initial", (result,), "Другой отзыв.")

    secondDraft = recovered.results.saveDraft(checkId, "teacher-review", (result,), "Уточнили отзыв.")
    assert secondDraft.resultRev == 2
    assert api.get(url, headers=headers).json()["reason"] == "awaiting_artifact"
    assert api.get(url, params={"resultRev": 1}, headers=headers).json() == first.model_dump(mode="json")
    second = recovered.publishCheckResult(checkId, 2, storage)
    secondRef = second.markdownRef
    assert secondRef.key != firstRef.key
    assert api.get(url, headers=headers).json() == second.model_dump(mode="json")
    assert api.get(url, params={"resultRev": 3}, headers=headers).status_code == 404
    storage.client.put_object(Bucket="results", Key=secondRef.key, Body=b"different bytes")
    assert api.get(url, headers=headers).status_code == 503
    assert api.get(url, params={"resultRev": 1}, headers=headers).json() == first.model_dump(mode="json")


def testInlineMarkupKeepsOnlySafeMath() -> None:
    assert inlineText(r"$x^2 + \sqrt{4}$") == r"$x^2 + \sqrt{4}$"
    source = "~~~\n# Заголовок " + r"<img src=x> [ссылка](https://test.invalid) $\href{https://test.invalid}{x}$"
    assert inlineText(source) == (
        r"\~\~\~ \# Заголовок \<img src\=x\> \[ссылка\]\(https\:\/\/test\.invalid\)"
        r" \$\\href\{https\:\/\/test\.invalid\}\{x\}\$"
    )
