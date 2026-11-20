"""Worker-to-registry reference registration, revision isolation and crash recovery."""

import hashlib
import json
import socket
from collections.abc import Callable, Iterator
from threading import Thread
from typing import Any

import pytest
import uvicorn
from redis import Redis
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1.events.checks import DATA_FIELD
from contracts.v1.references.reference import (
    ReferencePending, ReferenceReady, ReferenceRequest, ReferenceResult, ReferenceUnsupported, ReferenceValidationFailed,
)
from dataset.docxwriter import Paragraph, buildDocx
from grading.intake import IncomingDocRef
from grading.stream import StreamConsumer
from grading.tables import AssignmentJob, CheckJob
from grading.references import ReferenceUnavailableError
from grading.worker import REFERENCE_RETRY_LIMIT, GradingWorker
from references.registry.main import createApp
from references.registry.tables import ReferenceRecord
from references.settings import Settings
from shared.clock import ManualClock


@pytest.fixture
def registryClock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def liveRegistry(sessionFactory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, registryClock: ManualClock) -> Iterator[None]:
    token = "test-grading"
    rights = json.dumps({token: {"serviceName": "grading", "rights": ["references.request"]}})
    app = createApp(Settings(databaseUrl="", serviceTokensJson=rights), sessionFactory=sessionFactory, clock=registryClock)
    server = uvicorn.Server(uvicorn.Config(app, ws="none", log_config=None, access_log=False))
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        monkeypatch.setenv("GRADING_REFERENCE_URL", f"http://127.0.0.1:{listener.getsockname()[1]}")
        monkeypatch.setenv("GRADING_REFERENCE_TOKEN", token)
        thread = Thread(target=server.run, kwargs={"sockets": [listener]}, daemon=True)
        thread.start()
        try:
            yield
        finally:
            server.should_exit = True
            thread.join(timeout=5)
            assert not thread.is_alive()


def testPendingReferenceResumesByGetAfterRestart(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
    liveRegistry: None, registryClock: ManualClock,
) -> None:
    client, stream = gradingStream
    worker = GradingWorker(StreamConsumer(client, "initial", streamName=stream), gradingEngine, clock=registryClock.now)
    document = buildDocx("Variant", [Paragraph("1. Длина вектора (6; 8)")])
    event = contractExample("events/assignmentPublished.json")
    event["payload"].update(requirements=[])
    event["payload"]["docRef"]["sha256"] = hashlib.sha256(document).hexdigest()
    client.xadd(stream, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    lease = worker.jobs.claimAssignment()
    assert lease is not None
    pending = worker.prepareAssignment(lease, lambda _: document)["t1"]
    assert pending.state == "pending" and pending.retryAfterS == 10
    restarted = GradingWorker(StreamConsumer(client, "restarted", streamName=stream), gradingEngine, clock=registryClock.now)
    registryClock.advance(9)
    assert restarted.jobs.claimAssignment() is None
    client.xadd(stream, {DATA_FIELD: json.dumps(contractExample("events/attemptSubmitted.json"))})
    restarted.runBatch()
    assert restarted.jobs.claimCheck() is not None
    registryClock.advance(1)
    due = restarted.jobs.claimAssignment()
    assert due is not None

    def noPost(task: ReferenceRequest) -> ReferenceResult:
        raise AssertionError("pending reference must resume by GET")

    ready = restarted.prepareAssignment(due, lambda _: b"", requestReference=noPost)["t1"]
    assert ready.state == "ready" and ready.referenceId == pending.referenceId and ready.value == {"exact": "10"}


def testEarlyAttemptsShareOnlyTheReferencesOfTheirRevision(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
    liveRegistry: None, sessionFactory: sessionmaker[Session],
) -> None:
    client, stream = gradingStream
    clock = ManualClock()
    worker = GradingWorker(StreamConsumer(client, "preparer", streamName=stream), gradingEngine, clock=clock.now)
    assignment = buildDocx("Вариант", [Paragraph("1. Длина вектора (3; 4)"), Paragraph("2. Длина вектора (0; 0)")])
    submission = buildDocx("Работа", [Paragraph("Задание 1. Ответ: 5"), Paragraph("Задание 2. Ответ: 0")])

    def publish(rev: int) -> None:
        event = contractExample("events/assignmentPublished.json")
        event["eventId"] = f"assignment{rev}"
        event["payload"].update(assignmentRev=rev, requirements=[])
        event["payload"]["docRef"]["sha256"] = hashlib.sha256(assignment).hexdigest()
        client.xadd(stream, {DATA_FIELD: json.dumps(event)})
        worker.runBatch()

    def submit(number: int, rev: int) -> None:
        event = contractExample("events/attemptSubmitted.json")
        event["eventId"] = f"attempt{number}"
        event["payload"].update(checkId=f"c{number}", attemptId=f"a{number}", studentId=f"s{number}", assignmentRev=rev)
        event["payload"]["docRef"]["sha256"] = hashlib.sha256(submission).hexdigest()
        client.xadd(stream, {DATA_FIELD: json.dumps(event)})
        worker.runBatch()

    def noDocument(ref: IncomingDocRef) -> bytes:
        raise AssertionError("a waiting check must release its lease before loading the submission")

    submit(1, 1)
    early = worker.jobs.claimCheck()
    assert early is not None and worker.prepareCheck(early, noDocument) is None
    with Session(gradingEngine) as session:
        assert session.get_one(CheckJob, "c1").state == "waiting_reference"
    submit(2, 1)
    publish(1)
    lease = worker.jobs.claimAssignment()
    assert lease is not None
    refs = worker.prepareAssignment(lease, lambda _: assignment)
    assert set(refs) == {"t1", "t2"} and refs["t1"].referenceId != refs["t2"].referenceId
    assert isinstance(refs["t1"], ReferenceReady) and refs["t1"].value == {"exact": "5"}
    with sessionFactory() as session:
        records = session.scalars(select(ReferenceRecord).order_by(ReferenceRecord.taskId)).all()
        assert [(r.assignmentId, r.assignmentRev, r.taskId, r.statement, r.constraints) for r in records] == [
            ("w1", 1, "t1", "Длина вектора (3; 4)", {}), ("w1", 1, "t2", "Длина вектора (0; 0)", {}),
        ]

    restarted = GradingWorker(StreamConsumer(client, "checker", streamName=stream), gradingEngine, clock=clock.now)
    clock.advance(5)
    for _ in range(2):
        check = restarted.jobs.claimCheck()
        assert check is not None
        prepared = restarted.prepareCheck(check, lambda _: submission)
        assert prepared is not None and prepared.references == refs
        assert len(prepared.reading.steps) == 2
        assert restarted.jobs.finish(check, "ready", "test_complete")

    submit(3, 2)
    newCheck = restarted.jobs.claimCheck()
    assert newCheck is not None and restarted.prepareCheck(newCheck, noDocument) is None
    publish(2)
    newAssignment = restarted.jobs.claimAssignment()
    assert newAssignment is not None
    newRefs = restarted.prepareAssignment(newAssignment, lambda _: assignment)
    assert newRefs["t1"].referenceId != refs["t1"].referenceId
    clock.advance(5)
    retried = restarted.jobs.claimCheck()
    assert retried is not None
    prepared = restarted.prepareCheck(retried, lambda _: submission)
    assert prepared is not None and prepared.references == newRefs


def testInterruptedRegistrationResumesAtTheFirstMissingTask(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
) -> None:
    client, stream = gradingStream
    clock = ManualClock()
    worker = GradingWorker(StreamConsumer(client, "preparer", streamName=stream), gradingEngine, clock=clock.now)
    event = contractExample("events/assignmentPublished.json")
    client.xadd(stream, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    job = worker.jobs.claimAssignment()
    assert job is not None
    tasks = [
        {"taskId": "t1", "taskNumber": "1", "statement": "Первое", "requirements": []},
        {"taskId": "t2", "taskNumber": "2", "statement": "Второе", "requirements": [], "constraints": {"bounds": [0, 4]}},
    ]
    assert worker.jobs.checkpoint(job, "assignment_parsed", {"tasks": tasks})
    assert worker.jobs.reschedule(job, "queued", clock.now())
    parsed = worker.jobs.claimAssignment()
    assert parsed is not None
    calls: list[ReferenceRequest] = []

    def request(task: ReferenceRequest) -> ReferenceResult:
        calls.append(task)
        if task.taskId == "t1":
            return ReferenceReady(referenceId="r1", state="ready", source="fixture", schemaId="scalar.v1",
                                  value={"exact": "1"}, solverVersion=None)
        if len(calls) == 2:
            raise TimeoutError("interrupted")
        return ReferencePending(referenceId="r2", state="pending", retryAfterS=10)

    def noDocument(ref: IncomingDocRef) -> bytes:
        raise AssertionError("parsed tasks must survive the restart")

    with pytest.raises(TimeoutError):
        worker.prepareAssignment(parsed, noDocument, requestReference=request)
    clock.advance(61)
    resumed = worker.jobs.claimAssignment()
    assert resumed is not None
    refs = worker.prepareAssignment(resumed, noDocument, requestReference=request)
    assert [call.taskId for call in calls] == ["t1", "t2", "t2"]
    assert [call.constraints for call in calls] == [{}, {"bounds": [0, 4]}, {"bounds": [0, 4]}]
    assert {key: result.referenceId for key, result in refs.items()} == {"t1": "r1", "t2": "r2"}
    with Session(gradingEngine) as session:
        assert session.get_one(AssignmentJob, ("w1", 1)).state == "waiting_reference"


@pytest.mark.parametrize("terminal", [
    ReferenceUnsupported(referenceId="r1", state="unsupported", reasonCode="unknown_task"),
    ReferenceValidationFailed(referenceId="r1", state="validation_failed", reasonCode="independent_tests_failed"),
])
def testTerminalReferenceStateRoutesSubmissionToTeacherReview(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
    terminal: ReferenceUnsupported | ReferenceValidationFailed,
) -> None:
    client, stream = gradingStream
    clock = ManualClock()
    worker = GradingWorker(StreamConsumer(client, "terminal", streamName=stream), gradingEngine, clock=clock.now)
    document = buildDocx("Variant", [Paragraph("1. Длина вектора (3; 4)")])
    event = contractExample("events/assignmentPublished.json")
    event["payload"]["docRef"]["sha256"] = hashlib.sha256(document).hexdigest()
    client.xadd(stream, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    assignment = worker.jobs.claimAssignment()
    assert assignment is not None
    refs = worker.prepareAssignment(assignment, lambda _: document, requestReference=lambda _: terminal)
    assert refs["t1"] == terminal
    with Session(gradingEngine) as session:
        stored = session.get_one(AssignmentJob, ("w1", 1))
        assert stored.state == "needs_review"
        assert stored.stageData is not None
        assert stored.stageData["references"]["t1"]["reasonCode"] == terminal.reasonCode

    submitted = contractExample("events/attemptSubmitted.json")
    client.xadd(stream, {DATA_FIELD: json.dumps(submitted)})
    worker.runBatch()
    check = worker.jobs.claimCheck()
    assert check is not None
    assert worker.prepareCheck(check, lambda _: b"") is None
    with Session(gradingEngine) as session:
        assert session.get_one(CheckJob, "c1").state == "needs_review"


def testTransientReferenceFailureReschedulesAndThenUsesReadyResult(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
) -> None:
    client, stream = gradingStream
    clock = ManualClock()
    worker = GradingWorker(StreamConsumer(client, "transient", streamName=stream), gradingEngine, clock=clock.now)
    document = buildDocx("Variant", [Paragraph("1. Длина вектора (3; 4)")])
    event = contractExample("events/assignmentPublished.json")
    event["payload"]["docRef"]["sha256"] = hashlib.sha256(document).hexdigest()
    client.xadd(stream, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    assignment = worker.jobs.claimAssignment()
    assert assignment is not None
    calls = 0

    def request(_: ReferenceRequest) -> ReferenceResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ReferenceUnavailableError("temporary outage", retryAfterS=5)
        return ReferenceReady(referenceId="r1", state="ready", source="fixture", schemaId="scalar.v1", value={"exact": "5"}, solverVersion=None)

    assert worker.prepareAssignment(assignment, lambda _: document, requestReference=request) == {}
    assert calls == 1
    with Session(gradingEngine) as session:
        stored = session.get_one(AssignmentJob, ("w1", 1))
        assert stored.state == "waiting_reference" and stored.stageData is not None
        assert stored.stageData["referenceAttempts"] == {"t1": 1}
    assert worker.jobs.claimAssignment() is None
    clock.advance(5)
    retry = worker.jobs.claimAssignment()
    assert retry is not None
    refs = worker.prepareAssignment(retry, lambda _: document, requestReference=request)
    assert calls == 2 and refs["t1"].referenceId == "r1"


def testTransientReferenceFailureStopsAfterThreeAttempts(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: Callable[[str], Any],
) -> None:
    client, stream = gradingStream
    clock = ManualClock()
    worker = GradingWorker(StreamConsumer(client, "exhausted", streamName=stream), gradingEngine, clock=clock.now)
    document = buildDocx("Variant", [Paragraph("1. Длина вектора (3; 4)")])
    event = contractExample("events/assignmentPublished.json")
    event["payload"]["docRef"]["sha256"] = hashlib.sha256(document).hexdigest()
    client.xadd(stream, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    calls = 0

    def request(_: ReferenceRequest) -> ReferenceResult:
        nonlocal calls
        calls += 1
        raise ReferenceUnavailableError("temporary outage", retryAfterS=1)

    for attempt in range(REFERENCE_RETRY_LIMIT):
        lease = worker.jobs.claimAssignment()
        assert lease is not None
        assert worker.prepareAssignment(lease, lambda _: document, requestReference=request) == {}
        if attempt < REFERENCE_RETRY_LIMIT - 1:
            clock.advance(1)
    assert calls == REFERENCE_RETRY_LIMIT
    with Session(gradingEngine) as session:
        assert session.get_one(AssignmentJob, ("w1", 1)).state == "needs_review"
