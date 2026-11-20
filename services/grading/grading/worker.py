"""Consume grading events and acknowledge them after PostgreSQL commits."""

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from grading.inbox import SavedJob, saveEvent
from grading.stream import StreamConsumer, StreamDelivery


class GradingWorker:
    def __init__(self, consumer: StreamConsumer, engine: Engine) -> None:
        self.consumer = consumer
        self.engine = engine

    def runBatch(self, *, count: int = 10, blockMs: int | None = None) -> list[SavedJob]:
        return [self.persistDelivery(delivery) for delivery in self.consumer.readBatch(count=count, blockMs=blockMs)]

    def persistDelivery(self, delivery: StreamDelivery) -> SavedJob:
        with Session(self.engine) as session, session.begin():
            job = saveEvent(session, delivery.event)
        self.consumer.acknowledge(delivery.messageId)
        return job
