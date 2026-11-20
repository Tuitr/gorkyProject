"""Structured gateway log lines: one JSON object per event, with ids, codes and timings only.

Only the keys below may appear, so request text, model output or secrets cannot slip into a log line.
"""

import json
import logging

logger = logging.getLogger("references.gateway")

LogValue = str | int | float | bool | list[str] | None
SAFE_KEYS = frozenset(
    {
        "event",
        "stage",
        "code",
        "message",
        "requestId",
        "traceId",
        "attempt",
        "durationMs",
        "modelId",
        "providerRequestId",
        "retryAfterS",
        "stepIds",
        "problems",
    }
)


def logEvent(event: str, *, level: int = logging.INFO, **fields: LogValue) -> None:
    unknown = set(fields) - SAFE_KEYS
    if unknown:
        raise ValueError(f"not allowed in gateway logs: {sorted(unknown)}")
    record = {"event": event} | {key: value for key, value in fields.items() if value is not None}
    logger.log(level, json.dumps(record, ensure_ascii=False, sort_keys=True))
