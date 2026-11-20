"""The grading worker process: events in, published results out, and every failure ends somewhere.

The registry and the gateway run in uvicorn with their fixture provider and transport, as in the C7.3 handoff;
Redis, PostgreSQL and S3 are the real stand services. Documents come from a dict, the S3 source reader has
its own tests below.
"""

import hashlib
import json
import os
import socket
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import timedelta
from threading import Thread
from typing import Any
from uuid import uuid4

import boto3
import pytest
import uvicorn
from botocore.client import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.events.checks import DATA_FIELD
from contracts.v1.grading.assignmentreferences import AssignmentReferences
from contracts.v1.grading.checkresult import PublishedCheck
from dataset.generate import DEFAULT_SEED, GeneratedDataset, generateDataset
from grading.intake import IncomingDocRef
from grading.main import createApp as createGradingApi
from grading.markdownStorage import MarkdownStorage
from grading.referenceView import ReferenceReader
from grading.results import ResultStore
from grading.runner import GradingRunner, isLeaseLost, isPermanent
from grading.sources import SourceChanged, SourceDocuments, SourceUnavailable
from grading.stream import StreamConsumer
from grading.tables import AssignmentJob, CheckJob
from grading.settings import Settings as GradingSettings
from grading.worker import GradingWorker
from references.gateway.main import createApp as createGateway
from references.gateway.settings import GatewaySettings
from references.gateway.transports import gigachat
from references.gateway.transports.fixture import FixtureTransport
from references.registry.main import createApp as createRegistry
from references.settings import Settings
from shared.buckets import RESULTS_BUCKET, SOURCES_BUCKET

TOKEN = "grading-runner-token"


@contextmanager
def served(app: FastAPI) -> Iterator[str]:
    server = uvicorn.Server(uvicorn.Config(app, ws="none", log_config=None, access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{listener.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(timeout=5)


@pytest.fixture
def services(sessionFactory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Iterator[FixtureTransport]:
    def noPaidProvider(*args: object, **kwargs: object) -> None:
        raise AssertionError("the worker must not build the GigaChat transport")

    monkeypatch.setattr(gigachat.GigaChatTransport, "__init__", noPaidProvider)
    transport = FixtureTransport.fromFile()

    def rights(right: str) -> str:
        return json.dumps({TOKEN: {"serviceName": "grading", "rights": [right]}})

    registry = createRegistry(Settings(databaseUrl="unused", serviceTokensJson=rights("references.request")),
                              sessionFactory=sessionFactory)
    gateway = createGateway(GatewaySettings(transport="fixture", serviceTokensJson=rights("model.review")),
                            sessionFactory=sessionFactory, transport=transport)
    with served(registry) as registryUrl, served(gateway) as gatewayUrl:
        monkeypatch.setenv("GRADING_REFERENCE_URL", registryUrl)
        monkeypatch.setenv("GRADING_REFERENCE_TOKEN", TOKEN)
        monkeypatch.setenv("GRADING_GATEWAY_URL", gatewayUrl)
        monkeypatch.setenv("GRADING_GATEWAY_TOKEN", TOKEN)
        yield transport


def adminS3() -> Any:
    return boto3.client(
        "s3", endpoint_url=os.environ.get("S3_ENDPOINT_URL", "http://127.0.0.1:18333"),
        aws_access_key_id=os.environ.get("S3_ACCESS_KEY_ID", "gorky-dev"),
        aws_secret_access_key=os.environ.get("S3_SECRET_ACCESS_KEY", "gorky-dev-secret"),
        region_name=os.environ.get("S3_REGION", "us-east-1"),
        config=Config(proxies={}, s3={"addressing_style": "path"}),
    )


@pytest.fixture
def storage() -> Iterator[MarkdownStorage]:
    admin = adminS3()
    if not any(bucket.get("Name") == RESULTS_BUCKET for bucket in admin.list_buckets()["Buckets"]):
        admin.create_bucket(Bucket=RESULTS_BUCKET)
    markdown = MarkdownStorage.fromEnv()
    try:
        yield markdown
    finally:
        markdown.client.close()
        admin.close()


@pytest.fixture(scope="module")
def dataset() -> GeneratedDataset:
    return generateDataset(DEFAULT_SEED)


class Events:
    """Publishes checks.v1 events for one assignment revision into an isolated stream; documents stay in memory."""

    def __init__(self, stream: tuple[Redis, str], example: Callable[[str], Any]) -> None:
        self.client, self.streamName = stream
        self.example = example
        self.documents: dict[str, bytes] = {}
        self.assignmentId = f"w-{uuid4().hex}"

    def send(self, event: dict[str, Any], document: bytes) -> None:
        event["eventId"] = f"e-{uuid4().hex}"
        event["payload"]["assignmentId"] = self.assignmentId
        event["payload"]["docRef"]["sha256"] = hashlib.sha256(document).hexdigest()
        self.documents[event["payload"]["docRef"]["key"]] = document
        self.client.xadd(self.streamName, {DATA_FIELD: json.dumps(event)})

    def publish(self, document: bytes) -> None:
        event = self.example("events/assignmentPublished.json")
        event["payload"]["requirements"] = []
        self.send(event, document)

    def submit(self, document: bytes) -> str:
        checkId = f"c-{uuid4().hex}"
        event = self.example("events/attemptSubmitted.json")
        event["payload"].update(checkId=checkId, attemptId=f"a-{checkId}", studentId=f"s-{checkId}")
        event["payload"]["docRef"]["key"] = f"attempts/{checkId}.docx"
        self.send(event, document)
        return checkId

    def load(self, ref: IncomingDocRef) -> bytes:
        return self.documents[ref.key]


@pytest.fixture
def events(gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any]) -> Events:
    return Events(gradingStream, contractExample)


def buildRunner(engine: Engine, stream: tuple[Redis, str], load: Callable[[IncomingDocRef], bytes],
                storage: MarkdownStorage, **options: Any) -> GradingRunner:
    client, streamName = stream
    worker = GradingWorker(StreamConsumer(client, "runner", streamName=streamName), engine)
    return GradingRunner(worker, load, storage, **options)


def runUntil(runner: GradingRunner, done: Callable[[], bool], timeoutS: float = 30) -> None:
    deadline = time.monotonic() + timeoutS
    while not done():
        assert time.monotonic() < deadline, "the worker did not finish in time"
        if not runner.runOnce(blockMs=50):
            time.sleep(0.1)  # jobs that wait for the assignment come back after a few seconds


def jobState(engine: Engine, checkId: str) -> str | None:
    with Session(engine) as session:
        job = session.get(CheckJob, checkId)
        return None if job is None else job.state


def testWorkerPublishesBothDemoChecks(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], services: FixtureTransport, storage: MarkdownStorage,
    events: Events, dataset: GeneratedDataset,
) -> None:
    submissions = sorted(path for path in dataset.files if path.startswith("submissions/demo1/"))
    events.publish(dataset.files["assignments/demo1.docx"])
    checks = {events.submit(dataset.files[path]): path for path in submissions}
    runner = buildRunner(gradingEngine, gradingStream, events.load, storage)

    runUntil(runner, lambda: all(jobState(gradingEngine, checkId) in ("ready", "needs_review") for checkId in checks))

    results = ResultStore(gradingEngine)
    for checkId, path in checks.items():
        published = results.read(checkId, None, storage.require)
        assert isinstance(published, PublishedCheck), path
        expected = [case.expectedVerdict.answer for case in dataset.manifest.cases if case.submissionRef.file == path]
        assert [task.answer for task in published.taskResults] == expected, path
        assert published.resultRev == 1
        assert storage.download(published.markdownRef).decode().strip()
        assert jobState(gradingEngine, checkId) == published.state
    assert not runner.attempts

    # The teacher sees the reference answers of the registry through the results API.
    api = TestClient(createGradingApi(
        GradingSettings(serviceTokensJson=json.dumps({"cabinet": {"serviceName": "cabinet", "rights": ["checks.read"]}})),
        engine=gradingEngine, storage=storage,
    ))
    url = f"/internal/v1/assignments/{events.assignmentId}/references"
    shown = api.get(url, params={"assignmentRev": 1}, headers={"Authorization": "Bearer cabinet"})
    assert shown.status_code == 200, shown.text
    references = AssignmentReferences.model_validate(shown.json())
    assert references.state == "ready"
    assert {task.taskId: (task.state, task.answer) for task in references.tasks} == {
        "t1": ("ready", "17"), "t2": ("ready", "13"), "t3": ("ready", "7"),
    }
    assert api.get(url, params={"assignmentRev": 2}, headers={"Authorization": "Bearer cabinet"}).status_code == 404
    assert api.get(url).status_code == 401


def testUnreadableAssignmentSendsItsChecksToTheTeacher(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], services: FixtureTransport, storage: MarkdownStorage,
    events: Events,
) -> None:
    events.publish(b"not a word document")
    checkId = events.submit(b"not a word document either")
    runner = buildRunner(gradingEngine, gradingStream, events.load, storage)

    runUntil(runner, lambda: jobState(gradingEngine, checkId) == "needs_review")

    with Session(gradingEngine) as session:
        assignment = session.get_one(AssignmentJob, (events.assignmentId, 1))
        check = session.get_one(CheckJob, checkId)
    assert (assignment.state, assignment.completedStage) == ("failed", "grading_failed")
    assert assignment.stageData == {"error": "AssignmentReadError", "attempts": 1}
    assert check.completedStage == "assignment_failed"
    assert services.calls == []
    failed = ReferenceReader(gradingEngine).read(events.assignmentId, 1)
    assert failed is not None and (failed.state, failed.tasks) == ("failed", [])


def testTemporaryFailureRepeatsThenGivesUp(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], storage: MarkdownStorage, events: Events,
    dataset: GeneratedDataset,
) -> None:
    calls = []

    def unavailable(ref: IncomingDocRef) -> bytes:
        calls.append(ref.key)
        raise SourceUnavailable("S3 is down")

    events.publish(dataset.files["assignments/demo1.docx"])
    runner = buildRunner(gradingEngine, gradingStream, unavailable, storage, retryDelay=timedelta(0), maxAttempts=3)

    runner.runOnce(blockMs=50)
    with Session(gradingEngine) as session:
        job = session.get_one(AssignmentJob, (events.assignmentId, 1))
        assert (job.state, job.leaseUntil) == ("queued", None)
    for _ in range(5):
        runner.runOnce(blockMs=50)

    with Session(gradingEngine) as session:
        job = session.get_one(AssignmentJob, (events.assignmentId, 1))
    assert (job.state, job.stageData) == ("failed", {"error": "SourceUnavailable", "attempts": 3})
    assert len(calls) == 3


def testFailureKinds() -> None:
    assert isPermanent(SourceChanged("x")) and isPermanent(ValueError("bad docx"))
    assert not isPermanent(SourceUnavailable("x")) and not isPermanent(RuntimeError("model gateway returned HTTP 503"))
    assert isLeaseLost(RuntimeError("check lease expired before the parse could be saved"))
    assert not isLeaseLost(RuntimeError("model gateway returned HTTP 503"))


@pytest.fixture
def sourceObject() -> Iterator[tuple[IncomingDocRef, bytes]]:
    admin = adminS3()
    body = b"PK synthetic document"
    key = f"tests/{uuid4().hex}.docx"
    admin.put_object(Bucket=SOURCES_BUCKET, Key=key, Body=body)
    try:
        yield IncomingDocRef(bucket=SOURCES_BUCKET, key=key, sha256=hashlib.sha256(body).hexdigest()), body
    finally:
        admin.delete_object(Bucket=SOURCES_BUCKET, Key=key)
        admin.close()


def testSourceReaderReturnsOnlyTheExactBytes(sourceObject: tuple[IncomingDocRef, bytes]) -> None:
    ref, body = sourceObject
    sources = SourceDocuments.fromEnv()

    assert sources.load(ref) == body
    with pytest.raises(SourceChanged):
        sources.load(ref.model_copy(update={"sha256": "0" * 64}))
    with pytest.raises(SourceChanged):
        sources.load(ref.model_copy(update={"bucket": RESULTS_BUCKET}))
    with pytest.raises(SourceUnavailable):
        sources.load(ref.model_copy(update={"key": f"tests/{uuid4().hex}.docx"}))
