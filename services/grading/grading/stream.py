"""Read and validate newly delivered grading events from Redis Streams.

Open a binary client with Redis.from_url(redisUrl, protocol=2), pass it to
StreamConsumer and close it when the worker stops.
"""

import logging
from dataclasses import dataclass
from typing import cast

from redis import Redis
from redis.exceptions import ResponseError

from contracts.v1.events.checks import CONSUMER_GROUP, DATA_FIELD, STREAM_NAME
from grading.intake import EventRejected, IncomingEvent, InvalidEvent, readEvent

logger = logging.getLogger(__name__)

WireString = bytes | str
StreamFields = dict[WireString, WireString]
StreamEntries = list[tuple[WireString, StreamFields]]


@dataclass(frozen=True)
class StreamDelivery:
    messageId: str
    event: IncomingEvent


class StreamConsumer:
    """Use a synchronous Redis client with binary responses and the RESP2 response shape."""

    def __init__(self, client: Redis, consumerName: str, *, streamName: str = STREAM_NAME) -> None:
        self.client = client
        self.consumerName = consumerName
        self.streamName = streamName
        try:
            # A new group must also see events published before the worker started.
            self.client.xgroup_create(self.streamName, CONSUMER_GROUP, id="0-0", mkstream=True)
        except ResponseError as error:
            if not str(error).startswith("BUSYGROUP "):
                raise

    def readBatch(self, *, count: int = 10, blockMs: int | None = None) -> list[StreamDelivery]:
        """Return new validated deliveries, leaving them pending for the transactional inbox."""
        batches = cast(
            list[tuple[WireString, StreamEntries]],
            self.client.xreadgroup(
                CONSUMER_GROUP, self.consumerName, {self.streamName: ">"}, count=count, block=blockMs
            ),
        )
        deliveries: list[StreamDelivery] = []
        for _, entries in batches:
            for rawId, fields in entries:
                messageId = rawId.decode("ascii") if isinstance(rawId, bytes) else rawId
                delivery = self._readEntry(messageId, fields)
                if delivery is not None:
                    deliveries.append(delivery)
        return deliveries

    def _readEntry(self, messageId: str, fields: StreamFields) -> StreamDelivery | None:
        data = fields.get(DATA_FIELD.encode("ascii"), fields.get(DATA_FIELD))
        if data is None:
            self._reject(messageId, "missing_data")
            return None
        try:
            event = readEvent(data)
        except EventRejected as error:
            traceId = error.traceId if isinstance(error, InvalidEvent) else None
            self._reject(messageId, error.reason, traceId)
            return None
        logger.info("Validated grading event messageId=%s traceId=%.128r type=%s", messageId, event.traceId, event.type)
        return StreamDelivery(messageId=messageId, event=event)

    def acknowledge(self, messageId: str) -> None:
        self.client.xack(self.streamName, CONSUMER_GROUP, messageId)

    def _reject(self, messageId: str, reason: str, traceId: str | None = None) -> None:
        logger.warning("Rejected grading entry messageId=%s reason=%s traceId=%.128r", messageId, reason, traceId)
        self.acknowledge(messageId)
