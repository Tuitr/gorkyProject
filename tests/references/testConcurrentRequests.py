"""C3.2: parallel POSTs of one request end with one row and one referenceId.

Every worker below has its own session and so its own PostgreSQL connection,
the same as workers in different processes.
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

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


EXAMPLES = Path(openapi.__file__).parent / "examples" / "references"
WAIT_S = 10


class RacingProvider(FixtureProvider):
    """Holds each caller until all of them have missed the SELECT fast path, so their INSERTs race."""

    def __init__(self, parties: int) -> None:
        super().__init__(FixtureProvider.fromFile().fixtures)
        self.barrier = threading.Barrier(parties, timeout=WAIT_S)

    def start(self, task: ProviderTask) -> ProviderOutcome:
        self.barrier.wait()
        return super().start(task)


def buildRequest(**changes: Any) -> ReferenceRequest:
    body: dict[str, Any] = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8"))
    return ReferenceRequest.model_validate(body | changes)


def buildService(sessionFactory: sessionmaker[Session], provider: FixtureProvider) -> ReferenceService:
    return ReferenceService(ReferenceStore(sessionFactory), provider, clock=ManualClock())


def sendTogether(service: ReferenceService, requests: list[ReferenceRequest]) -> list[ReferenceResult]:
    with ThreadPoolExecutor(max_workers=len(requests)) as pool:
        futures = [pool.submit(service.request, request) for request in requests]
        return [future.result(timeout=WAIT_S) for future in futures]


def countRecords(sessionFactory: sessionmaker[Session]) -> int:
    with sessionFactory() as session:
        return session.scalar(select(func.count()).select_from(ReferenceRecord)) or 0


@pytest.mark.parametrize(
    "statement",
    ["Длина вектора (3; 4)", "Длина вектора (6; 8)", "Длина вектора (1; 1)"],
    ids=["ready", "pending", "unsupported"],
)
def testParallelSameRequestsShareOneRecord(sessionFactory: sessionmaker[Session], statement: str) -> None:
    """Ready, pending and unsupported results all keep one row."""
    service = buildService(sessionFactory, RacingProvider(parties=2))

    first, second = sendTogether(service, [buildRequest(statement=statement), buildRequest(statement=statement)])

    assert first == second
    assert countRecords(sessionFactory) == 1


def testParallelRequestsWithOtherConstraintsGetTwoRecords(sessionFactory: sessionmaker[Session]) -> None:
    service = buildService(sessionFactory, RacingProvider(parties=2))

    first, second = sendTogether(service, [buildRequest(), buildRequest(constraints={"answerForm": "decimal"})])

    assert first.referenceId != second.referenceId
    assert countRecords(sessionFactory) == 2


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
