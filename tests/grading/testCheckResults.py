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
from sqlalchemy.orm import Session

from contracts.v1.events.checks import DATA_FIELD
from contracts.v1.grading.assignmentreport import AssignmentReport
from contracts.v1.grading.checkresult import PublishedCheck
from contracts.v1.grading.reviewpublication import PublishReviewReceipt
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
from grading.tables import CheckResultRevision
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
        for rev in (1, 2, 3, 4):
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
    published["payload"]["taskPoints"] = [{"taskNumber": "1", "maxScore": 3}]
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

    settings = Settings(serviceTokensJson=json.dumps({
        "test": {"serviceName": "cabinet", "rights": ["checks.read", "checks.edit", "checks.publish", "reports.read"]},
        "read-only": {"serviceName": "reader", "rights": ["checks.read"]},
    }))
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
    assert api.get(url, headers=headers).json() == {
        "checkId": checkId, "attemptId": "a1", "state": "running", "reason": "awaiting_artifact",
    }

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
    assert first.total and (first.total.score, first.total.maxScore, first.total.pendingCount) == (0, 3, 0)
    assert first.grade is None and first.teacherComment is None
    assert first.taskAssessments[0].comment == issue.comment
    assert [item.text for item in first.recommendations] == [issue.correction]
    firstRef = first.markdownRef
    loaded = storage.client.get_object(Bucket="results", Key=firstRef.key)
    with closing(loaded["Body"]) as streamBody:
        report = streamBody.read().decode("utf-8")
    assert loaded["ContentDisposition"] == 'attachment; filename="review-1.md"'
    assert "## Общий отзыв" in report and "Ответ отличается от эталона" in report
    assert "### Шаг s1" in report and "$2^2 + 1 = 4$" in report
    assert "Вычислите сумму" in report and "$2^2 + 1 = 5$" in report
    publishUrl = url + "/publish"
    downloadUrl = url + "/markdown"
    publication = {"resultRev": 1, "actorId": "teacher"}
    assert api.get(downloadUrl, params={"rev": 1}, headers=headers).status_code == 404
    assert api.post(publishUrl, headers={"Authorization": "Bearer read-only"}, json=publication).status_code == 403
    published = api.post(publishUrl, headers=headers, json=publication)
    assert published.status_code == 200
    assert PublishReviewReceipt.model_validate(published.json()) == PublishReviewReceipt(
        checkId=checkId, resultRev=1, markdownRef=firstRef,
    )
    with Session(gradingEngine) as session:
        initialAcceptance = session.get_one(CheckResultRevision, (checkId, 1)).acceptedAt
    assert initialAcceptance is not None
    assert api.post(publishUrl, headers=headers, json=publication).json() == published.json()
    with Session(gradingEngine) as session:
        accepted = session.get_one(CheckResultRevision, (checkId, 1))
        assert accepted.acceptedAt == initialAcceptance and accepted.acceptedBy == "teacher"
    first = first.model_copy(update={"publishedResultRev": 1})
    assert resume() == first
    with pytest.raises(ArtifactUnavailable, match="bytes differ"):
        storage.save(checkId, 1, "Этот текст нельзя положить по ключу опубликованного отчёта.")
    assert api.get(url, headers=headers).json() == first.model_dump(mode="json", exclude_unset=True)
    with pytest.raises(ResultConflict):
        recovered.results.saveDraft(checkId, "initial", (result,), "Другой отзыв.")

    reportUrl = "/internal/v1/assignments/w1/report"
    before = AssignmentReport.model_validate(api.get(reportUrl, headers=headers).json())
    assert before.tasks[0].categories[0].category == "arithmetic"
    edit = {"baseRev": 1, "editId": "fix-arithmetic", "actorId": "teacher", "changes": [
        {"op": "removeIssue", "taskId": "t1", "issueId": "i1"},
        {"op": "setVerdict", "taskId": "t1", "answer": "correct", "reasoning": "valid"},
    ]}
    with Stubber(storage.client) as s3Failure:
        s3Failure.add_client_error("put_object", service_error_code="ServiceUnavailable", http_status_code=503)
        assert api.patch(url + "/review", headers=headers, json=edit).status_code == 503
        s3Failure.assert_no_pending_responses()
    assert api.get(url, headers=headers).json()["reason"] == "awaiting_artifact"
    assert api.post(publishUrl, headers=headers, json={"resultRev": 2}).status_code == 409
    assert api.get(downloadUrl, params={"rev": 2}, headers=headers).status_code == 404
    assert api.get(downloadUrl, params={"rev": 1}, headers=headers).content == report.encode("utf-8")
    assert api.get(reportUrl, headers=headers).json() == before.model_dump(mode="json")
    assert api.get(url, params={"resultRev": 1}, headers=headers).json() == first.model_dump(mode="json", exclude_unset=True)
    receipt = api.patch(url + "/review", headers=headers, json=edit)
    assert receipt.status_code == 200 and receipt.json()["resultRev"] == 2
    second = PublishedCheck.model_validate(api.get(url, headers=headers).json())
    secondRef = second.markdownRef
    assert secondRef.key != firstRef.key
    assert "Ответ верный." in second.overallReview and "Вычислите сумму" not in second.overallReview
    with closing(storage.client.get_object(Bucket="results", Key=secondRef.key)["Body"]) as streamBody:
        revised = streamBody.read().decode("utf-8")
    assert "Вычислите сумму" not in revised and "Ответ верный" in revised
    with closing(storage.client.get_object(Bucket="results", Key=firstRef.key)["Body"]) as streamBody:
        assert streamBody.read() == report.encode("utf-8")
    after = AssignmentReport.model_validate(api.get(reportUrl, headers=headers).json())
    assert after.rev == before.rev + 1 and after.students[0].resultRev == 2
    assert after.tasks[0].categories == []
    assert api.get(reportUrl, params={"rev": before.rev}, headers=headers).json() == before.model_dump(mode="json")
    assert api.get(url, params={"resultRev": 3}, headers=headers).status_code == 404
    withoutDeleted = api.get(reportUrl, params={"assignmentRev": 1, "excludeCheckId": checkId}, headers=headers)
    assert withoutDeleted.status_code == 200 and withoutDeleted.json()["students"] == []
    assert api.get(reportUrl, params={"assignmentRev": 2}, headers=headers).status_code == 404
    assert api.get(reportUrl, params={"rev": 1, "excludeCheckId": checkId}, headers=headers).status_code == 422

    assert second.total and second.total.score == 3 and not second.recommendations
    for op in [{"op": "setGrade", "grade": 1}, {"op": "setGrade", "grade": True},
               {"op": "setTaskScore", "taskId": "t1", "score": 4},
               {"op": "setTaskScore", "taskId": "t1", "score": -1}]:
        invalid = {"baseRev": 2, "editId": "invalid", "actorId": "teacher", "changes": [op]}
        assert api.patch(url + "/review", headers=headers, json=invalid).status_code == 422
    manualText = "Мой отзыв о решении."
    manual = {"baseRev": 2, "editId": "manual", "actorId": "teacher", "changes": [
        {"op": "setOverallReview", "overallReview": manualText},
        {"op": "setGrade", "grade": 4},
        {"op": "setTeacherComment", "teacherComment": "Проверьте вычисления"},
        {"op": "setTaskScore", "taskId": "t1", "score": 1.5},
    ]}
    assert api.patch(url + "/review", headers=headers, json=manual).json()["resultRev"] == 3
    assert api.patch(url + "/review", headers=headers, json=manual).json()["resultRev"] == 3
    later = {"baseRev": 3, "editId": "method", "actorId": "teacher", "changes": [
        {"op": "setVerdict", "taskId": "t1", "method": "met"},
    ]}
    assert api.patch(url + "/review", headers=headers, json=later).json()["resultRev"] == 4
    assert api.get(url, headers=headers).json()["overallReview"] == manualText
    assert api.get(downloadUrl, params={"rev": 2}, headers=headers).status_code == 404
    publishedSecond = api.post(publishUrl, headers=headers, json={"resultRev": 2, "actorId": "teacher"})
    assert publishedSecond.status_code == 200 and publishedSecond.json()["resultRev"] == 2
    assert api.get(downloadUrl, params={"rev": 4}, headers=headers).status_code == 404
    download = api.get(downloadUrl, params={"rev": 2}, headers=headers)
    assert download.status_code == 200 and download.headers["Content-Type"] == "text/markdown; charset=utf-8"
    assert download.content == revised.encode("utf-8")
    oldDownload = api.get(downloadUrl, params={"rev": 1}, headers=headers)
    assert oldDownload.content == report.encode("utf-8")
    assert oldDownload.headers["Content-Disposition"] == 'attachment; filename="review-1.md"'
    storage.client.put_object(Bucket="results", Key=secondRef.key, Body=b"different bytes")
    assert api.get(url, params={"resultRev": 2}, headers=headers).status_code == 503
    assert api.get(downloadUrl, params={"rev": 2}, headers=headers).status_code == 503
    assert api.get(url, params={"resultRev": 1}, headers=headers).json() == first.model_dump(mode="json", exclude_unset=True) | {
        "publishedResultRev": 2,
    }, "publishedResultRev is the latest revision the teacher published, whichever revision is read"

    scoreRev = api.get(url, headers=headers).json()
    assert scoreRev["total"] == {"score": 1.5, "maxScore": 3, "pendingCount": 0}
    assert scoreRev["grade"] == 4 and scoreRev["teacherComment"] == "Проверьте вычисления"
    assert api.post(publishUrl, headers=headers, json={"resultRev": 4, "actorId": "teacher"}).status_code == 200
    gradedMarkdown = api.get(downloadUrl, params={"rev": 4}, headers=headers).text
    assert gradedMarkdown.rstrip().endswith("Оценка — 4") and "Проверьте вычисления" in gradedMarkdown
    clear = {"baseRev": 4, "editId": "clear", "actorId": "teacher", "changes": [
        {"op": "setGrade", "grade": None}, {"op": "setTeacherComment", "teacherComment": None},
        {"op": "setTaskScore", "taskId": "t1", "score": None},
    ]}
    assert api.patch(url + "/review", headers=headers, json=clear).json()["resultRev"] == 5
    current = api.get(url, headers=headers).json()
    assert current["grade"] is None and current["teacherComment"] is None
    assert current["taskAssessments"][0]["score"] is None
    assert current["total"] == {"score": 0, "maxScore": 3, "pendingCount": 1}
    assert api.get(downloadUrl, params={"rev": 5}, headers=headers).status_code == 404
    assert api.get(downloadUrl, params={"rev": 4}, headers=headers).text == gradedMarkdown
    assert api.get(downloadUrl, params={"rev": 1}, headers=headers).text == report


def testInlineMarkupKeepsOnlySafeMath() -> None:
    assert inlineText(r"$x^2 + \sqrt{4}$") == r"$x^2 + \sqrt{4}$"
    source = "~~~\n# Заголовок " + r"<img src=x> [ссылка](https://test.invalid) $\href{https://test.invalid}{x}$"
    assert inlineText(source) == (
        r"\~\~\~ \# Заголовок \<img src\=x\> \[ссылка\]\(https\:\/\/test\.invalid\)"
        r" \$\\href\{https\:\/\/test\.invalid\}\{x\}\$"
    )
