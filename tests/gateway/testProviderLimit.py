"""C4.3: one provider call at a time for all gateway processes; 429/503 repeated on schedule, then refused."""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from contracts.v1 import openapi
from contracts.v1.references.modelreview import ModelReviewRequest
from references.gateway.limiter import LimitedTransport, ProviderLimiter, RetryPolicy, Slot, Wait
from references.gateway.prompts import PromptCatalog
from references.gateway.sanitize import buildModelCall
from references.gateway.settings import GatewaySettings
from references.gateway.tables import ProviderSlotRecord
from references.gateway.transports.base import ConfigError, ModelCall, ModelReply, TransportError
from shared.clock import ManualClock

EXAMPLES = Path(openapi.__file__).parent / "examples" / "gateway"
WAIT_S = 10
REPLY = ModelReply(content="{}", modelId="fixture")


def buildCall(requestId: str = "q1") -> ModelCall:
    body = json.loads((EXAMPLES / "request.json").read_text(encoding="utf-8")) | {"requestId": requestId}
    return buildModelCall(ModelReviewRequest.model_validate(body), PromptCatalog())


def buildLimiter(sessionFactory: sessionmaker[Session], clock: ManualClock, limit: int = 1) -> ProviderLimiter:
    return ProviderLimiter(sessionFactory, clock, "fixture", limit, leaseS=90)


def heldSlots(sessionFactory: sessionmaker[Session]) -> list[str | None]:
    with sessionFactory() as session:
        query = select(ProviderSlotRecord.holderRequestId).where(ProviderSlotRecord.leaseToken.is_not(None))
        return list(session.scalars(query))


class Scripted:
    """Answers with the scripted outcomes in order, then REPLY; notes the clock time of every call."""

    name: Literal["fixture"] = "fixture"

    def __init__(self, clock: ManualClock, *outcomes: BaseException) -> None:
        self.clock = clock
        self.outcomes = list(outcomes)
        self.calledAt: list[float] = []

    def complete(self, call: ModelCall) -> ModelReply:
        self.calledAt.append(self.clock.now().timestamp() - START)
        if self.outcomes:
            raise self.outcomes.pop(0)
        return REPLY


START = ManualClock().now().timestamp()


def buildLimited(
    sessionFactory: sessionmaker[Session], clock: ManualClock, inner: Scripted, maxAttempts: int = 3
) -> LimitedTransport:
    policy = RetryPolicy(maxAttempts=maxAttempts, maxWaitS=30, defaultBackoffS=2, pollS=0.5)
    return LimitedTransport(inner, buildLimiter(sessionFactory, clock), clock, policy)


def testSecondProcessWaitsForTheSlot(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    first, second = buildLimiter(sessionFactory, clock), buildLimiter(sessionFactory, clock)

    slot = first.tryAcquire("q1")
    blocked = second.tryAcquire("q2")
    assert isinstance(slot, Slot)
    first.release(slot)
    afterRelease = second.tryAcquire("q2")

    assert blocked == Wait(90.0, "busy")
    assert isinstance(afterRelease, Slot)
    assert heldSlots(sessionFactory) == ["q2"]


def testExpiredLeaseCannotFreeTheNextHoldersSlot(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    crashed, other, third = (buildLimiter(sessionFactory, clock) for _ in range(3))

    old = crashed.tryAcquire("q1")
    clock.advance(91)
    taken = other.tryAcquire("q2")
    assert isinstance(old, Slot) and isinstance(taken, Slot)
    crashed.release(old)

    assert old.slotNo == taken.slotNo
    assert heldSlots(sessionFactory) == ["q2"]
    assert isinstance(third.tryAcquire("q3"), Wait)


def testLimitOfTwoAllowsTwoHolders(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    limiter = buildLimiter(sessionFactory, clock, limit=2)

    outcomes = [limiter.tryAcquire(requestId) for requestId in ("q1", "q2", "q3")]

    assert [type(outcome) for outcome in outcomes] == [Slot, Slot, Wait]


class SharedClock(ManualClock):
    """One controlled time for two threads; sleep also yields so the other thread can move."""

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        time.sleep(0.005)


class ConcurrencyProbe:
    name: Literal["fixture"] = "fixture"

    def __init__(self, clock: SharedClock) -> None:
        self.clock = clock
        self.lock = threading.Lock()
        self.active = 0
        self.maxActive = 0
        self.calls: list[str] = []

    def complete(self, call: ModelCall) -> ModelReply:
        with self.lock:
            self.active += 1
            self.maxActive = max(self.maxActive, self.active)
            self.calls.append(call.requestId)
        deadline = time.monotonic() + WAIT_S
        while len(self.calls) == 1 and not self.clock.sleeps and time.monotonic() < deadline:
            time.sleep(0.005)
        with self.lock:
            self.active -= 1
        return REPLY


def testTwoClientsNeverCallTheProviderTogether(sessionFactory: sessionmaker[Session]) -> None:
    clock = SharedClock()
    probe = ConcurrencyProbe(clock)
    policy = RetryPolicy(maxWaitS=30, pollS=0.5)
    clients = [LimitedTransport(probe, buildLimiter(sessionFactory, clock), clock, policy) for _ in range(2)]

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.complete, buildCall(f"q{index}")) for index, client in enumerate(clients)]
        replies = [future.result(timeout=WAIT_S) for future in futures]

    assert replies == [REPLY, REPLY]
    assert probe.maxActive == 1
    assert sorted(probe.calls) == ["q0", "q1"]
    assert clock.sleeps
    assert heldSlots(sessionFactory) == []


class SlotWatchingClock(ManualClock):
    """Notes which slots are held at every pause."""

    def __init__(self, sessionFactory: sessionmaker[Session]) -> None:
        super().__init__()
        self.sessionFactory = sessionFactory
        self.heldDuringPause: list[list[str | None]] = []

    def sleep(self, seconds: float) -> None:
        self.heldDuringPause.append(heldSlots(self.sessionFactory))
        super().sleep(seconds)


def testAfter429TheNextCallRunsOnSchedule(sessionFactory: sessionmaker[Session]) -> None:
    clock = SlotWatchingClock(sessionFactory)
    inner = Scripted(clock, TransportError("rate_limited", "GigaChat chat answered HTTP 429", 7))

    reply = buildLimited(sessionFactory, clock, inner).complete(buildCall())

    assert reply == REPLY
    assert inner.calledAt == [0, 7]
    assert clock.sleeps == [7]
    assert clock.heldDuringPause == [[]]


def testOtherProcessAlsoWaitsAfter429(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    first = buildLimiter(sessionFactory, clock)
    slot = first.tryAcquire("q1")
    assert isinstance(slot, Slot)
    first.release(slot, backoffS=7)

    assert buildLimiter(sessionFactory, clock).tryAcquire("q2") == Wait(7.0, "backoff")


def testExhaustedAttemptsAreAnExplicitRefusal(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    tooMany = [TransportError("rate_limited", "GigaChat chat answered HTTP 429", 2) for _ in range(5)]
    inner = Scripted(clock, *tooMany)

    with pytest.raises(TransportError) as raised:
        buildLimited(sessionFactory, clock, inner).complete(buildCall())

    assert inner.calledAt == [0, 2, 4]
    assert raised.value.code == "rate_limited"
    assert raised.value.retryAfterS == 2
    assert "after 3 attempts" in raised.value.message
    assert heldSlots(sessionFactory) == []


def testUnavailableWithoutRetryAfterUsesTheDefaultPause(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    inner = Scripted(clock, TransportError("unavailable", "GigaChat chat answered HTTP 503"))

    buildLimited(sessionFactory, clock, inner).complete(buildCall())

    assert inner.calledAt == [0, 2]


def testPauseLongerThanTheBudgetIsRefusedAtOnce(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    inner = Scripted(clock, TransportError("rate_limited", "GigaChat chat answered HTTP 429", 60))

    with pytest.raises(TransportError) as raised:
        buildLimited(sessionFactory, clock, inner).complete(buildCall())

    assert inner.calledAt == [0]
    assert clock.sleeps == []
    assert (raised.value.code, raised.value.retryAfterS) == ("rate_limited", 60)


def testBusySlotBeyondTheBudgetIsRefused(sessionFactory: sessionmaker[Session]) -> None:
    clock = ManualClock()
    holder = buildLimiter(sessionFactory, clock).tryAcquire("q0")
    assert isinstance(holder, Slot)
    inner = Scripted(clock)

    with pytest.raises(TransportError) as raised:
        buildLimited(sessionFactory, clock, inner).complete(buildCall())

    assert inner.calledAt == []
    assert raised.value.code == "rate_limited"
    assert sum(clock.sleeps) <= 30
    assert heldSlots(sessionFactory) == ["q0"]


@pytest.mark.parametrize(
    "failure",
    [TransportError("timeout", "GigaChat chat timed out"), TransportError("internal_error", "HTTP 400"), KeyboardInterrupt()],
    ids=["timeout", "notRepeated", "stop"],
)
def testSlotIsFreedOnFailureAndStop(sessionFactory: sessionmaker[Session], failure: BaseException) -> None:
    clock = ManualClock()
    inner = Scripted(clock, failure)

    with pytest.raises(type(failure)):
        buildLimited(sessionFactory, clock, inner).complete(buildCall())

    assert inner.calledAt == [0]
    assert heldSlots(sessionFactory) == []


def testSettingsComputeTheReviewLease() -> None:
    settings = GatewaySettings.fromEnv(
        {"GATEWAY_TRANSPORT": "fixture", "GATEWAY_LEASE_S": "60", "GATEWAY_REQUEST_BUDGET_S": "40"}
    )

    assert (settings.providerLimit, settings.formatAttempts, settings.reviewLeaseS) == (1, 2, 50)


@pytest.mark.parametrize(("name", "value"), [("GATEWAY_PROVIDER_LIMIT", "0"), ("GATEWAY_MAX_ATTEMPTS", "1.5")])
def testBadLimitsDoNotStart(name: str, value: str) -> None:
    with pytest.raises(ConfigError, match=name):
        GatewaySettings.fromEnv({"GATEWAY_TRANSPORT": "fixture", name: value})


def testSlotsComeBackAfterTheTableIsCleared(sessionFactory: sessionmaker[Session]) -> None:
    limiter = buildLimiter(sessionFactory, ManualClock())
    first = limiter.tryAcquire("q1")
    assert isinstance(first, Slot)
    limiter.release(first)
    with sessionFactory() as session, session.begin():
        session.query(ProviderSlotRecord).delete()

    assert limiter.tryAcquire("q2") == Wait(0, "busy"), "no slot row: ask again instead of failing"
    again = limiter.tryAcquire("q2")
    assert isinstance(again, Slot) and heldSlots(sessionFactory) == ["q2"]
