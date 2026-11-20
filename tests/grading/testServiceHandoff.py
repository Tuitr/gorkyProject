"""C7.3: developer 2's worker against developer 3's services over real HTTP.

The reference registry and the model gateway run in uvicorn on free local ports, with the fixture provider and
the fixture transport. The worker reads the generated mini set (assignment demo1 and its two submissions) and
talks to them only through ReferenceClient and sendReview, so serialization and HTTP codes are checked on both
sides. Three scenarios: a ready result, pending -> ready, and manual review with the registry's reasonCode.
No paid provider is called: the GigaChat transport refuses to be built during these tests.

Run:  uv run pytest tests/grading/testServiceHandoff.py
Contract examples it relies on: examples/events/assignmentPublished.json, examples/events/attemptSubmitted.json,
examples/references/exchanges/{readyOpenSet,pendingThenReady,unsupported,validationFailed}.json and the open-set
replies of fixtures/reviews.json (examples/gateway/exchanges/*.json).
"""

import hashlib
import json
import socket
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from threading import Thread
from typing import Any

import pytest
import uvicorn
from fastapi import FastAPI, Request, Response
from redis import Redis
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.events.checks import DATA_FIELD
from contracts.v1.grading.taskresult import TaskResult
from contracts.v1.references.reference import ReferenceReady
from dataset.docxwriter import Paragraph, buildDocx
from dataset.generate import DEFAULT_SEED, GeneratedDataset, generateDataset
from grading.stream import StreamConsumer
from grading.tables import AssignmentJob, CheckJob
from grading.worker import GradingWorker, referenceErrorsAdapter
from references.gateway.main import createApp as createGateway
from references.gateway.settings import GatewaySettings
from references.gateway.transports import gigachat
from references.gateway.transports.fixture import FixtureTransport
from references.registry.main import createApp as createRegistry
from references.settings import Settings
from shared.clock import ManualClock

TOKEN = "grading-handoff-token"
Hits = list[tuple[str, str, int]]


@contextmanager
def served(app: FastAPI) -> Iterator[tuple[str, Hits]]:
    """The app on a free 127.0.0.1 port; yields its URL and every (method, path, status) it answered."""
    hits: Hits = []

    @app.middleware("http")
    async def record(request: Request, callNext: Callable[[Request], Any]) -> Response:
        response: Response = await callNext(request)
        hits.append((request.method, request.url.path, response.status_code))
        return response

    server = uvicorn.Server(uvicorn.Config(app, ws="none", log_config=None, access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{listener.getsockname()[1]}", hits
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


class Stand:
    def __init__(self, registryHits: Hits, gatewayHits: Hits, transport: FixtureTransport, clock: ManualClock) -> None:
        self.registryHits = registryHits
        self.gatewayHits = gatewayHits
        self.transport = transport
        self.clock = clock


@pytest.fixture
def stand(sessionFactory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch) -> Iterator[Stand]:
    def noPaidProvider(*args: object, **kwargs: object) -> None:
        raise AssertionError("the handoff must not build the GigaChat transport")

    monkeypatch.setattr(gigachat.GigaChatTransport, "__init__", noPaidProvider)
    clock = ManualClock()
    transport = FixtureTransport.fromFile()

    def rights(right: str) -> str:
        return json.dumps({TOKEN: {"serviceName": "grading", "rights": [right]}})

    registry = createRegistry(
        Settings(databaseUrl="unused", serviceTokensJson=rights("references.request")),
        sessionFactory=sessionFactory,
        clock=clock,
    )
    gateway = createGateway(
        GatewaySettings(transport="fixture", serviceTokensJson=rights("model.review")),
        sessionFactory=sessionFactory,
        transport=transport,
        clock=clock,
    )
    with served(registry) as (registryUrl, registryHits), served(gateway) as (gatewayUrl, gatewayHits):
        monkeypatch.setenv("GRADING_REFERENCE_URL", registryUrl)
        monkeypatch.setenv("GRADING_REFERENCE_TOKEN", TOKEN)
        monkeypatch.setenv("GRADING_GATEWAY_URL", gatewayUrl)
        monkeypatch.setenv("GRADING_GATEWAY_TOKEN", TOKEN)
        yield Stand(registryHits, gatewayHits, transport, clock)


@pytest.fixture(scope="module")
def dataset() -> GeneratedDataset:
    return generateDataset(DEFAULT_SEED)


class Worker:
    """Publishes events into an isolated stream and drives developer 2's worker through one job at a time."""

    def __init__(
        self, engine: Engine, stream: tuple[Redis, str], example: Callable[[str], Any], clock: ManualClock
    ) -> None:
        self.client, self.streamName = stream
        self.example = example
        consumer = StreamConsumer(self.client, "handoff", streamName=self.streamName)
        self.worker = GradingWorker(consumer, engine, clock=clock.now)
        self.documents: dict[str, bytes] = {}

    def send(self, event: dict[str, Any], document: bytes) -> None:
        event["payload"]["docRef"]["sha256"] = hashlib.sha256(document).hexdigest()
        self.documents[event["payload"]["docRef"]["key"]] = document
        self.client.xadd(self.streamName, {DATA_FIELD: json.dumps(event)})
        self.worker.runBatch()

    def load(self, ref: Any) -> bytes:
        return self.documents[ref.key]

    def publish(self, document: bytes) -> None:
        event = self.example("events/assignmentPublished.json")
        event["payload"].update(requirements=[])
        self.send(event, document)

    def submit(self, number: int, document: bytes) -> None:
        event = self.example("events/attemptSubmitted.json")
        event["eventId"] = f"attempt{number}"
        event["payload"].update(checkId=f"c{number}", attemptId=f"a{number}", studentId=f"s{number}")
        event["payload"]["docRef"]["key"] = f"attempts/a{number}.docx"
        self.send(event, document)

    def prepareAssignment(self) -> dict[str, Any]:
        lease = self.worker.jobs.claimAssignment()
        assert lease is not None
        return self.worker.prepareAssignment(lease, self.load)

    def review(self) -> tuple[TaskResult, ...] | None:
        lease = self.worker.jobs.claimCheck()
        assert lease is not None
        return self.worker.reviewCheck(lease, self.load)


@pytest.fixture
def worker(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any], stand: Stand
) -> Worker:
    return Worker(gradingEngine, gradingStream, contractExample, stand.clock)


def verdict(result: TaskResult) -> dict[str, Any]:
    return {
        "answer": result.answer,
        "reasoning": result.reasoning,
        "method": result.method,
        "needsReview": result.needsReview,
        "issues": [(issue.issueId, issue.stepId, issue.category, issue.causedByIssueId) for issue in result.issues],
    }


def expectedVerdicts(dataset: GeneratedDataset, submissionFile: str) -> list[dict[str, Any]]:
    return [
        case.expectedVerdict.model_dump(mode="json")
        | {"issues": [(i.issueId, i.stepId, i.category, i.causedByIssueId) for i in case.expectedIssues]}
        for case in dataset.manifest.cases
        if case.submissionRef.file == submissionFile
    ]


def testReadyReferencesAndReviewsReachTheWorkerOverHttp(
    worker: Worker, stand: Stand, dataset: GeneratedDataset
) -> None:
    submissions = sorted(path for path in dataset.files if path.startswith("submissions/demo1/"))
    worker.publish(dataset.files["assignments/demo1.docx"])

    references = worker.prepareAssignment()
    results = []
    for number, path in enumerate(submissions, start=1):
        worker.submit(number, dataset.files[path])
        results.append(worker.review())

    assert {taskId: ref.value for taskId, ref in references.items() if isinstance(ref, ReferenceReady)} == {
        "t1": {"exact": "17"}, "t2": {"exact": "13"}, "t3": {"exact": "7"}
    }
    for path, taskResults in zip(submissions, results, strict=True):
        assert taskResults is not None
        assert [verdict(result) for result in taskResults] == expectedVerdicts(dataset, path), path
    assert [hit for hit in stand.registryHits if hit[0] == "POST"] == [("POST", "/internal/v1/references", 200)] * 3
    reviewed = sum(
        1 for case in dataset.manifest.cases if case.split == "demo" and case.expectedVerdict.answer != "skipped"
    )
    assert stand.gatewayHits == [("POST", "/internal/v1/model/review", 200)] * reviewed
    assert len(stand.transport.calls) == reviewed, "one recorded reply per reviewed task, no other provider"


def testPendingReferenceBecomesReadyOnTheNextPoll(worker: Worker, stand: Stand, gradingEngine: Engine) -> None:
    worker.publish(buildDocx("Вариант", [Paragraph("1. Длина вектора (6; 8)")]))

    pending = worker.prepareAssignment()["t1"]
    stand.clock.advance(pending.retryAfterS)
    ready = worker.prepareAssignment()["t1"]

    assert (pending.state, pending.retryAfterS) == ("pending", 10)
    assert isinstance(ready, ReferenceReady) and ready.referenceId == pending.referenceId
    assert ready.value == {"exact": "10"}
    assert stand.registryHits == [
        ("POST", "/internal/v1/references", 202),
        ("GET", f"/internal/v1/references/{pending.referenceId}", 200),
    ]
    with Session(gradingEngine) as session:
        assert session.get_one(AssignmentJob, ("w1", 1)).state == "ready"


def testUnavailableReferencesSendOnlyThoseTasksToManualReview(
    worker: Worker, stand: Stand, gradingEngine: Engine, dataset: GeneratedDataset
) -> None:
    worker.publish(buildDocx("Вариант", [
        Paragraph("1. Найдите длину вектора a⃗ = (15; −8)."),
        Paragraph("2. Найдите угол между векторами a⃗ = (1; 0) и b⃗ = (0; 1)."),
        Paragraph("3. Длина вектора (5; 12)"),
    ]))
    worker.prepareAssignment()
    stand.clock.advance(10)
    references = worker.prepareAssignment()
    worker.submit(1, buildDocx("Работа", [
        Paragraph("Задание 1."), Paragraph("Ответ: 17"),
        Paragraph("Задание 2."), Paragraph("Ответ: 90°"),
        Paragraph("Задание 3."), Paragraph("Ответ: 13"),
    ]))

    results = worker.review()

    assert {taskId: (ref.state, getattr(ref, "reasonCode", None)) for taskId, ref in references.items()} == {
        "t1": ("ready", None),
        "t2": ("unsupported", "unknown_task"),
        "t3": ("validation_failed", "independent_tests_failed"),
    }
    with Session(gradingEngine) as session:
        assignment = session.get_one(AssignmentJob, ("w1", 1))
        check = session.get_one(CheckJob, "c1")
        assert assignment.state == "needs_review"
        assert check.stageData is not None
        errors = referenceErrorsAdapter.validate_python(check.stageData["referenceErrors"])
        assert {taskId: error["reasonCode"] for taskId, error in errors.items()} == {
            "t2": "unknown_task", "t3": "independent_tests_failed"
        }
    assert results is not None
    byTask = {result.taskId: result for result in results}
    assert (byTask["t2"].needsReview, byTask["t3"].needsReview) == (True, True)
    assert byTask["t1"].answer == "correct", "the ready neighbour is still reviewed"
    assert stand.gatewayHits == [("POST", "/internal/v1/model/review", 200)]
