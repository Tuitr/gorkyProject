"""The grading worker process: `uv run python -m grading.runner`.

One pass reads new and idle checks.v1 deliveries into the inbox, then works off every due job:
an assignment revision is parsed and its references requested; a check is parsed, reviewed through
the model gateway, stored as a result revision and published as Markdown.

Failures are sorted by kind. A temporary one (HTTP, Redis, S3, an unavailable service) repeats the job
after retryDelay, at most maxAttempts times in this process; a permanent one (a document that cannot be
read, changed bytes, invalid data) ends the job as failed, so the cabinet shows it to the teacher instead
of waiting forever. A lost lease means another worker owns the job now: nothing is written.
Logs carry the job key, traceId and the error class, never document text.
"""

import logging
import os
import signal
import socket
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from types import FrameType

from redis import Redis
from sqlalchemy import create_engine

from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from grading.intake import IncomingDocRef
from grading.jobs import JobKey, JobLease
from grading.markdownStorage import MarkdownStorage
from grading.results import ResultConflict
from grading.review import sendReview
from grading.settings import Settings
from grading.sources import SourceDocuments
from grading.stream import StreamConsumer
from grading.worker import GradingWorker
from shared.dotenv import loadDotEnv

logger = logging.getLogger("grading.runner")

DEFAULT_LEASE = timedelta(minutes=10)
"""Longer than one check can take: every task goes through the gateway, 50 s at most each (C4.3)."""


def isPermanent(error: Exception) -> bool:
    """Invalid input never gets better by repeating; a lost lease is not a failure of the job."""
    return isinstance(error, ValueError) and not isinstance(error, ResultConflict)


def isLeaseLost(error: Exception) -> bool:
    return isinstance(error, ResultConflict) or (isinstance(error, RuntimeError) and "lease expired" in str(error))


@dataclass
class GradingRunner:
    worker: GradingWorker
    loadDocument: Callable[[IncomingDocRef], bytes]
    storage: MarkdownStorage
    transport: Callable[[ModelReviewRequest], ModelReviewResponse] = sendReview
    leaseFor: timedelta = DEFAULT_LEASE
    retryDelay: timedelta = timedelta(seconds=10)
    maxAttempts: int = 5
    attempts: Counter[JobKey] = field(default_factory=Counter)

    def runOnce(self, *, blockMs: int = 1_000) -> int:
        """Read events, then work off every due job. Returns the number of jobs worked on."""
        self.worker.runBatch(count=10, blockMs=blockMs)
        seen: set[JobKey] = set()
        for claim, work in (
            (self.worker.jobs.claimAssignment, lambda current: self.worker.prepareAssignment(current, self.loadDocument)),
            (self.worker.jobs.claimCheck, self.gradeCheck),
        ):
            while (lease := claim(leaseFor=self.leaseFor)) is not None:
                if lease.key in seen:
                    # Due again within this pass (a zero delay): give it back and let the next pass take it.
                    self.worker.jobs.reschedule(lease, "queued", datetime.now(UTC))
                    break
                seen.add(lease.key)
                self.attempt(lease, work)
        return len(seen)

    def gradeCheck(self, lease: JobLease) -> None:
        # Publishing the result also closes the job (CheckResults.publish), so nothing is left to record here.
        # None means the check waits for its assignment or was handed to the teacher; that state is saved too.
        self.worker.completeCheck(lease, self.loadDocument, self.storage, transport=self.transport)

    def attempt(self, lease: JobLease, work: Callable[[JobLease], object]) -> None:
        try:
            work(lease)
        except Exception as error:
            self.recordFailure(lease, error)
        else:
            self.attempts.pop(lease.key, None)

    def recordFailure(self, lease: JobLease, error: Exception) -> None:
        kind = type(error).__name__
        if isLeaseLost(error):
            logger.warning("job %s lost its lease (traceId %s): %s", lease.key, lease.traceId, kind)
            return
        self.attempts[lease.key] += 1
        attempt = self.attempts[lease.key]
        if isPermanent(error) or attempt >= self.maxAttempts:
            self.attempts.pop(lease.key, None)
            self.worker.jobs.finish(lease, "failed", "grading_failed", {"error": kind, "attempts": attempt})
            logger.error("job %s failed for good (traceId %s) after %d attempts: %s", lease.key, lease.traceId, attempt, kind)
            return
        nextAt = datetime.now(UTC) + self.retryDelay * attempt
        self.worker.jobs.reschedule(lease, "queued", nextAt)
        logger.warning("job %s will be repeated (traceId %s), attempt %d: %s", lease.key, lease.traceId, attempt, kind)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    loadDotEnv()
    settings = Settings.fromEnv()
    engine = create_engine(settings.databaseUrl, hide_parameters=True, pool_pre_ping=True)
    redis = Redis.from_url(os.environ.get("REDIS_URL", "redis://127.0.0.1:16379/0"), protocol=2)
    consumerName = os.environ.get("GRADING_WORKER_NAME") or socket.gethostname()
    runner = GradingRunner(
        GradingWorker(StreamConsumer(redis, consumerName), engine),
        SourceDocuments.fromEnv().load,
        MarkdownStorage.fromEnv(),
    )
    stopping = False

    def stop(signum: int, frame: FrameType | None) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    logger.info("grading worker %s started", consumerName)
    try:
        while not stopping:
            try:
                runner.runOnce()
            except Exception:
                logger.exception("grading pass failed; retrying")
                time.sleep(2)
    finally:
        redis.close()
        engine.dispose()


if __name__ == "__main__":
    main()
