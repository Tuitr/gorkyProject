"""Forward committed outbox events to Redis Streams."""

import json
import logging
from collections.abc import Callable

from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from sqlalchemy import create_engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from cabinet.outbox import OutboxRecord
from cabinet.settings import Settings
from contracts.v1.events.checks import DATA_FIELD, STREAM_NAME
from shared.clock import Clock, SystemClock

LOGGER = logging.getLogger(__name__)
IDLE_DELAY_S = 0.5
RETRY_DELAY_S = 2.0


def appendToStream(redis: Redis, data: str) -> None:
    entryId = redis.xadd(STREAM_NAME, {DATA_FIELD: data})
    if not isinstance(entryId, (str, bytes)) or not entryId:
        raise RedisError("XADD did not acknowledge the outbox event")


def publishNext(
    sessions: sessionmaker[Session], append: Callable[[str], None], clock: Clock,
) -> bool:
    with sessions.begin() as db:
        record = db.scalar(
            select(OutboxRecord)
            .where(OutboxRecord.publishedAt.is_(None))
            .order_by(OutboxRecord.sequence)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if record is None:
            return False

        data = json.dumps(record.data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        append(data)
        # A failure between XADD and this commit leaves the same event eligible for retry.
        record.publishedAt = clock.now()
    return True


def runPublisher(
    sessions: sessionmaker[Session], append: Callable[[str], None], clock: Clock,
) -> None:
    while True:
        try:
            published = publishNext(sessions, append, clock)
        except (RedisError, SQLAlchemyError) as error:
            LOGGER.warning("Outbox publication will retry (%s)", type(error).__name__)
            clock.sleep(RETRY_DELAY_S)
        else:
            if not published:
                clock.sleep(IDLE_DELAY_S)


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = Settings.fromEnv()
    engine = create_engine(settings.databaseUrl, pool_pre_ping=True)
    try:
        with Redis.from_url(
            settings.redisUrl, socket_connect_timeout=2, socket_timeout=2, retry=Retry(NoBackoff(), 0),
        ) as redis:
            def append(data: str) -> None:
                appendToStream(redis, data)

            try:
                runPublisher(sessionmaker(engine), append, SystemClock())
            except KeyboardInterrupt:
                return
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
