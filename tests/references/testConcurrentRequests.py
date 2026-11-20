"""C3.2: parallel POSTs of one request end with one row, one referenceId and one provider call.

Every worker below has its own session and so its own PostgreSQL connection,
the same as workers in different processes.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from collections.abc import Callable
from typing import Any, TypeVar

import pytest
from alembic import command
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from contracts.v1.references.reference import ReferenceRequest, ReferenceResult
from references.database import buildAlembicConfig
from references.providers.base import ProviderOutcome, ProviderTask
from references.providers.fixture import FixtureProvider
from references.registry.keys import buildLookupKey
from references.registry.service import ReferenceService
from references.registry.store import ReferenceStore
from references.registry.tables import ReferenceRecord
from shared.clock import ManualClock


T = TypeVar("T")
R = TypeVar("R")
PARTIES = 4
EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"
WAIT_S = 10


class CountingProvider(FixtureProvider):
    """Counts start() and check() calls; each call takes a little time, so a second caller would overlap."""

    def __init__(self) -> None:
        super().__init__(FixtureProvider.fromFile().fixtures)
        self.lock = threading.Lock()
        self.starts = 0
        self.checks = 0

    def start(self, task: ProviderTask) -> ProviderOutcome:
        with self.lock:
            self.starts += 1
        time.sleep(0.1)
        return super().start(task)

    def check(self, task: ProviderTask) -> ProviderOutcome:
        with self.lock:
            self.checks += 1
        time.sleep(0.1)
        return super().check(task)


def holdAfter(original: Callable[[T], R], parties: int) -> Callable[[T], R]:
    """Let every caller finish the unlocked read before any of them goes on, so all of them miss the fast path."""
    barrier = threading.Barrier(parties, timeout=WAIT_S)

    def held(argument: T) -> R:
        result = original(argument)
        barrier.wait()
        return result

    return held


def buildRequest(**changes: Any) -> ReferenceRequest:
    body: dict[str, Any] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    return ReferenceRequest.model_validate(body | changes)


def buildService(sessionFactory: sessionmaker[Session], provider: FixtureProvider) -> ReferenceService:
    return ReferenceService(ReferenceStore(sessionFactory), provider, clock=ManualClock())


def sendTogether(call: Callable[[T], R], arguments: list[T]) -> list[R]:
    with ThreadPoolExecutor(max_workers=len(arguments)) as pool:
        futures = [pool.submit(call, argument) for argument in arguments]
        return [future.result(timeout=WAIT_S) for future in futures]


def countRecords(sessionFactory: sessionmaker[Session]) -> int:
    with sessionFactory() as session:
        return session.scalar(select(func.count()).select_from(ReferenceRecord)) or 0


@pytest.mark.parametrize(
    "statement",
    ["Длина вектора (3; 4)", "Длина вектора (6; 8)", "Длина вектора (1; 1)"],
    ids=["ready", "pending", "unsupported"],
)
def testParallelSameRequestsShareOneRecord(
    sessionFactory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch, statement: str
) -> None:
    """Ready, pending and unsupported results all keep one row, and the provider is asked once."""
    provider = CountingProvider()
    service = buildService(sessionFactory, provider)
    monkeypatch.setattr(service, "findSame", holdAfter(service.findSame, PARTIES))

    results = sendTogether(service.request, [buildRequest(statement=statement) for _ in range(PARTIES)])

    assert all(result == results[0] for result in results)
    assert countRecords(sessionFactory) == 1
    assert provider.starts == 1


def testParallelRequestsWithOtherConstraintsGetTwoRecords(
    sessionFactory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    provider = CountingProvider()
    service = buildService(sessionFactory, provider)
    monkeypatch.setattr(service, "findSame", holdAfter(service.findSame, 2))

    first, second = sendTogether(service.request, [buildRequest(), buildRequest(constraints={"answerForm": "decimal"})])

    assert first.referenceId != second.referenceId
    assert countRecords(sessionFactory) == 2
    assert provider.starts == 2


def testParallelReadsOfADuePendingRecordCheckOnce(
    sessionFactory: sessionmaker[Session], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Many GETs after the promised wait ask the provider once; all of them see the new state."""
    provider = CountingProvider()
    clock = ManualClock()
    service = ReferenceService(ReferenceStore(sessionFactory), provider, clock=clock)
    pending = service.request(buildRequest(statement="Длина вектора (6; 8)"))
    assert pending.state == "pending"
    clock.advance(pending.retryAfterS)
    monkeypatch.setattr(service.store, "get", holdAfter(service.store.get, PARTIES))

    results = sendTogether(service.get, [pending.referenceId] * PARTIES)

    assert {result.state for result in results if result is not None} == {"ready"}
    assert len([result for result in results if result is not None]) == PARTIES
    assert provider.checks == 1


def testRequestWaitsForTheUncommittedTwin(engine: Engine, sessionFactory: sessionmaker[Session]) -> None:
    """A second worker arriving while the first one's INSERT is not committed yet gets the first referenceId."""
    request = buildRequest(statement="Длина вектора (6; 8)")
    provider = FixtureProvider.fromFile()
    twin = buildService(sessionFactory, provider)
    twin.idFactory = lambda: "ref_first"
    service = buildService(sessionFactory, provider)

    with sessionFactory() as firstSession, ThreadPoolExecutor(max_workers=1) as pool:
        firstSession.begin()
        firstSession.add(twin.buildRecord(request, provider.start(ProviderTask.fromRequest(request))))
        firstSession.flush()
        late = pool.submit(service.request, request)
        waitForBlockedInsert(engine)
        firstSession.commit()
        result = late.result(timeout=WAIT_S)

    assert result.referenceId == "ref_first"
    assert result.state == "pending"
    assert countRecords(sessionFactory) == 1


def waitForBlockedInsert(engine: Engine) -> None:
    deadline = time.monotonic() + WAIT_S
    with engine.connect() as connection:
        while time.monotonic() < deadline:
            if connection.scalar(text("SELECT count(*) FROM pg_locks WHERE NOT granted")):
                return
            connection.rollback()
            time.sleep(0.02)
    pytest.fail("the second INSERT never waited for the first transaction")


def testMigrationRefusesDuplicateKeys(engine: Engine, databaseUrl: str) -> None:
    config = buildAlembicConfig(databaseUrl)
    lookupKey = buildLookupKey(buildRequest())
    command.downgrade(config, "0002")
    try:
        with engine.begin() as connection:
            for referenceId in ("dup1", "dup2"):
                connection.execute(
                    text(
                        "INSERT INTO refs.reference_records (reference_id, lookup_key, assignment_id, assignment_rev,"
                        " task_id, statement, constraints, state, reason_code)"
                        " VALUES (:id, :key, 'w1', 1, 't1', 's', '{}', 'unsupported', 'unknown_task')"
                    ),
                    {"id": referenceId, "key": lookupKey},
                )
        with pytest.raises(RuntimeError, match="dup1, dup2"):
            command.upgrade(config, "head")
    finally:
        with engine.begin() as connection:
            connection.execute(text("DELETE FROM refs.reference_records WHERE reference_id IN ('dup1', 'dup2')"))
        command.upgrade(config, "head")
