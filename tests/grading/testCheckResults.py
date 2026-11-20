"""Persist a check through a worker retry and publish it with its S3 artifact."""

import hashlib
import json
import os
from collections.abc import Callable, Iterator
from typing import Any
from uuid import uuid4

import boto3
import pytest
from botocore.client import Config
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine

from contracts.v1.events.checks import DATA_FIELD
from contracts.v1.grading.checkresult import MarkdownRef
from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from contracts.v1.references.reference import ReferenceReady
from dataset.docxwriter import Paragraph, buildDocx
from grading.main import createApp
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
    prefix = f"tests/b51/{uuid4().hex}"
    storage = MarkdownStorage.fromEnv()
    try:
        yield storage, prefix
    finally:
        for rev in (1, 2):
            admin.delete_object(Bucket="results", Key=f"{prefix}/{rev}.md")
        storage.client.close()
        admin.close()


def testRecoversDraftAndReadsPublishedRevisions(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], resultStorage: tuple[MarkdownStorage, str],
    contractExample: Callable[[str], Any],
) -> None:
    stream, streamName = gradingStream
    clock = ManualClock()
    worker = GradingWorker(StreamConsumer(stream, "initial", streamName=streamName), gradingEngine, clock=clock.now)
    assignment = buildDocx("Вариант", [Paragraph("1. Найдите 2 + 3.")])
    submission = buildDocx("Работа", [Paragraph("Задание 1. Ответ: 5")])
    published = contractExample("events/assignmentPublished.json")
    published["payload"]["requirements"] = []
    published["payload"]["docRef"]["sha256"] = hashlib.sha256(assignment).hexdigest()
    submitted = contractExample("events/attemptSubmitted.json")
    submitted["payload"]["docRef"]["sha256"] = hashlib.sha256(submission).hexdigest()
    for event in (published, submitted):
        stream.xadd(streamName, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    assignmentLease = worker.jobs.claimAssignment()
    assert assignmentLease is not None
    reference = ReferenceReady(referenceId="r1", state="ready", source="fixture", schemaId="scalar.v1",
                               value={"exact": "5"}, solverVersion=None)
    worker.prepareAssignment(assignmentLease, lambda _: assignment, requestReference=lambda _: reference)

    storage, prefix = resultStorage
    settings = Settings(serviceTokensJson=json.dumps({"test": {"serviceName": "cabinet", "rights": ["checks.read"]}}))
    api = TestClient(createApp(settings, engine=gradingEngine, storage=storage))
    url = "/internal/v1/checks/c1"
    headers = {"Authorization": "Bearer test"}
    assert api.get(url).status_code == 401
    assert api.get(url, headers=headers).json()["state"] == "queued"
    lease = worker.jobs.claimCheck()
    assert lease is not None
    result = TaskResult(taskId="t1", answer="correct", reasoning="not_shown", method="optional",
                        issues=[], needsReview=False)

    def assess(request: ModelReviewRequest) -> ModelReviewResponse:
        assert request.taskId == "t1"
        return ModelReviewResponse(schemaVersion="taskResult.v1", taskResult=result, modelId="fixture")

    draft = worker.calculateCheck(lease, lambda _: submission, overallReview="Верный ответ.", transport=assess)
    assert draft is not None and draft.resultRev == 1 and draft.taskResults == [result]
    assert api.get(url, headers=headers).json() == {"checkId": "c1", "state": "running", "reason": "awaiting_artifact"}

    clock.advance(61)
    recovered = GradingWorker(StreamConsumer(stream, "recovered", streamName=streamName), gradingEngine, clock=clock.now)
    retry = recovered.jobs.claimCheck()
    assert retry is not None
    assert recovered.calculateCheck(
        retry, lambda _: pytest.fail("draft already calculated"), overallReview="",
        transport=lambda _: pytest.fail("review already completed"),
    ) == draft

    text = "Верное решение.".encode()
    firstRef = MarkdownRef(bucket="results", key=f"{prefix}/1.md", sha256=hashlib.sha256(text).hexdigest())
    with pytest.raises(ArtifactUnavailable):
        recovered.results.publish("c1", 1, firstRef, storage.require)
    assert api.get(url, headers=headers).json()["reason"] == "awaiting_artifact"
    storage.client.put_object(Bucket="results", Key=firstRef.key, Body=text)
    first = recovered.results.publish("c1", 1, firstRef, storage.require)
    assert first.state == "ready" and first.attemptId == "a1"
    assert recovered.results.publish("c1", 1, firstRef, storage.require) == first
    assert api.get(url, headers=headers).json() == first.model_dump(mode="json")
    with pytest.raises(ResultConflict):
        recovered.results.saveDraft("c1", "initial", (result,), "Другой отзыв.")

    secondDraft = recovered.results.saveDraft("c1", "teacher-review", (result,), "Уточнили отзыв.")
    assert secondDraft.resultRev == 2
    assert api.get(url, headers=headers).json()["reason"] == "awaiting_artifact"
    assert api.get(url, params={"resultRev": 1}, headers=headers).json() == first.model_dump(mode="json")
    secondRef = firstRef.model_copy(update={"key": f"{prefix}/2.md"})
    storage.client.put_object(Bucket="results", Key=secondRef.key, Body=text)
    second = recovered.results.publish("c1", 2, secondRef, storage.require)
    assert api.get(url, headers=headers).json() == second.model_dump(mode="json")
    assert api.get(url, params={"resultRev": 3}, headers=headers).status_code == 404
    storage.client.put_object(Bucket="results", Key=secondRef.key, Body=b"different bytes")
    assert api.get(url, headers=headers).status_code == 503
    assert api.get(url, params={"resultRev": 1}, headers=headers).json() == first.model_dump(mode="json")
