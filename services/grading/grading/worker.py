"""Consume grading events and acknowledge them after PostgreSQL commits."""

from collections.abc import Callable
from datetime import datetime

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from grading.inbox import SavedJob, saveEvent
from grading.jobs import JobQueue
from grading.stream import StreamConsumer, StreamDelivery


class GradingWorker:
    def __init__(
        self, consumer: StreamConsumer, engine: Engine, *, pendingIdleMs: int = 60_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.consumer = consumer
        self.engine = engine
        self.pendingIdleMs = pendingIdleMs
        self.jobs = JobQueue(engine, clock=clock)

    def runBatch(self, *, count: int = 10, blockMs: int | None = None) -> list[SavedJob]:
        """Persist pending and fresh deliveries. A zero block uses a one-second poll."""
        pending = self.consumer.readPending(minIdleMs=self.pendingIdleMs, count=count)
        saved = [self.persistDelivery(delivery) for delivery in pending]
        remaining = count - len(saved)
        if remaining:
            # Return periodically so the next pass can reclaim deliveries that have just become idle.
            wait = None if saved else (1_000 if blockMs == 0 else blockMs)
            saved.extend(
                self.persistDelivery(delivery) for delivery in self.consumer.readBatch(count=remaining, blockMs=wait)
            )
        return saved

    def persistDelivery(self, delivery: StreamDelivery) -> SavedJob:
        with Session(self.engine) as session, session.begin():
            job = saveEvent(session, delivery.event)
        self.consumer.acknowledge(delivery.messageId)
        return job
