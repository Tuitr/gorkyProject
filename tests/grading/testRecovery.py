"""B1.3 recovery across worker restarts with a controlled lease clock."""

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from redis import Redis
from redis.exceptions import ConnectionError as RedisConnectionError
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from contracts.v1.events.checks import CONSUMER_GROUP, DATA_FIELD
from grading.inbox import SavedCheck
from grading.jobs import FinalState
from grading.stream import StreamConsumer
from grading.tables import CheckJob, InboxEntry
from grading.worker import GradingWorker
from shared.clock import ManualClock

ExampleReader = Callable[[str], Any]


def testRestartReclaimsAnAlreadyCommittedRedisDelivery(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: ExampleReader, monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, streamName = gradingStream
    oldConsumer = StreamConsumer(client, "old", streamName=streamName)
    oldWorker = GradingWorker(oldConsumer, gradingEngine)
    event = contractExample("events/attemptSubmitted.json")
    client.xadd(streamName, {DATA_FIELD: json.dumps(event)})

    def failAck(messageId: str) -> None:
        raise RedisConnectionError("lost XACK")

    monkeypatch.setattr(oldConsumer, "acknowledge", failAck)
    with pytest.raises(RedisConnectionError, match="lost XACK"):
        oldWorker.runBatch()
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 1

    restarted = GradingWorker(StreamConsumer(client, "new", streamName=streamName), gradingEngine, pendingIdleMs=0)
    assert restarted.runBatch() == [SavedCheck(event["payload"]["checkId"], "queued")]
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 0
    assert restarted.runBatch() == []
    lease = restarted.jobs.claimCheck()
    assert lease is not None and lease.key == event["payload"]["checkId"]
    assert restarted.jobs.finish(lease, "ready", "finished")


@pytest.mark.parametrize("finalState", ["ready", "needs_review", "failed"])
def testRestartAfterAckResumesExpiredLeaseAndKeepsFinalStateOnLateDelivery(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: ExampleReader, finalState: FinalState,
) -> None:
    client, streamName = gradingStream
    clock = ManualClock()
    first = GradingWorker(StreamConsumer(client, "first", streamName=streamName), gradingEngine, clock=clock.now)
    event = contractExample("events/attemptSubmitted.json")
    checkId = event["payload"]["checkId"]
    client.xadd(streamName, {DATA_FIELD: json.dumps(event)})
    assert first.runBatch() == [SavedCheck(checkId, "queued")]
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 0

    restarted = GradingWorker(StreamConsumer(client, "second", streamName=streamName), gradingEngine, clock=clock.now)
    assert restarted.runBatch() == []
    original = restarted.jobs.claimCheck(leaseFor=timedelta(seconds=5))
    assert original is not None
    assert original.completedStage is None
    with Session(gradingEngine) as session:
        assert session.get_one(CheckJob, checkId).state == "running"
    assert restarted.jobs.checkpoint(original, "parsed", {"steps": ["s1"]})

    replacement = GradingWorker(StreamConsumer(client, "third", streamName=streamName), gradingEngine, clock=clock.now)
    assert replacement.jobs.claimCheck() is None
    clock.advance(5)
    assert not restarted.jobs.finish(original, "ready", "finished", {"worker": "expired"})
    resumed = replacement.jobs.claimCheck()
    assert resumed is not None
    assert resumed.key == original.key == checkId
    assert resumed.version == original.version + 1
    assert resumed.completedStage == "parsed"
    assert resumed.stageData == {"steps": ["s1"]}
    assert not restarted.jobs.checkpoint(original, "overwritten")
    assert replacement.jobs.finish(resumed, finalState, "finished", {"worker": "replacement"})
    assert not replacement.jobs.finish(resumed, finalState, "finished", {"worker": "duplicate"})

    # The event can arrive again with the original ID or a new envelope for the same check.
    late = {**event, "eventId": "late-submission"}
    for delivery in (event, late):
        client.xadd(streamName, {DATA_FIELD: json.dumps(delivery)})
    assert replacement.runBatch() == [SavedCheck(checkId, finalState), SavedCheck(checkId, finalState)]
    assert client.xpending(streamName, CONSUMER_GROUP)["pending"] == 0
    assert replacement.jobs.claimCheck() is None
    with Session(gradingEngine) as session:
        assert session.scalar(select(func.count()).select_from(CheckJob)) == 1
        assert session.scalar(select(func.count()).select_from(InboxEntry)) == 2
        job = session.get_one(CheckJob, checkId)
        assert job.leaseVersion == resumed.version and job.leaseUntil is None
        assert (job.state, job.completedStage, job.stageData) == (finalState, "finished", {"worker": "replacement"})


def testWaitingAssignmentResumesOnlyAtNextAttempt(
    gradingEngine: Engine, gradingStream: tuple[Redis, str], contractExample: ExampleReader,
) -> None:
    client, streamName = gradingStream
    clock = ManualClock()
    worker = GradingWorker(StreamConsumer(client, "preparer", streamName=streamName), gradingEngine, clock=clock.now)
    event = contractExample("events/assignmentPublished.json")
    client.xadd(streamName, {DATA_FIELD: json.dumps(event)})
    worker.runBatch()
    job = worker.jobs.claimAssignment()
    assert job is not None
    assert job.key == (event["payload"]["assignmentId"], event["payload"]["assignmentRev"])
    assert worker.jobs.checkpoint(job, "reference_requested", {"referenceId": "r1"})
    assert worker.jobs.reschedule(job, "waiting_reference", clock.now() + timedelta(seconds=10))
    clock.advance(9)
    assert worker.jobs.claimAssignment() is None
    clock.advance(1)
    retried = worker.jobs.claimAssignment()
    assert retried is not None
    assert retried.completedStage == "reference_requested"
    assert retried.stageData == {"referenceId": "r1"}
    assert not worker.jobs.finish(job, "ready", "stale")
    assert worker.jobs.finish(retried, "needs_review", "reference_refused")
    assert worker.jobs.claimAssignment() is None
