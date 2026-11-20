"""One limit on concurrent provider calls for all gateway processes, kept in refs.provider_slots.

A call runs only while it holds a slot lease. The lease has a token: releasing with an old token after
the lease expired and went to another call changes nothing. A crashed process frees its slot when the
lease runs out. Waiting for a slot or for Retry-After never holds a slot.

LimitedTransport wraps any transport: it repeats 429 and 503 (with Retry-After, or a default pause) up to
maxAttempts, never longer than maxWaitS in total and never past the deadline of the call, then refuses
explicitly. A timeout is not repeated here: the provider may already have done the work, so the caller
decides (same requestId, C3.3).
"""

import math
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from sqlalchemy import or_, select, update
from sqlalchemy.dialects.postgresql import insert

from references.gateway.tables import ProviderSlotRecord
from references.gateway.transports.base import ModelCall, ModelReply, ModelTransport, TransportError, TransportName
from references.registry.store import SessionFactory
from shared.clock import Clock

REPEATED_CODES = frozenset({"rate_limited", "unavailable"})


@dataclass(frozen=True)
class Slot:
    provider: str
    slotNo: int
    leaseToken: str


@dataclass(frozen=True)
class Wait:
    seconds: float
    reason: Literal["backoff", "busy"]


@dataclass(frozen=True)
class RetryPolicy:
    maxAttempts: int = 3
    maxWaitS: float = 30.0
    defaultBackoffS: float = 2.0
    pollS: float = 0.5


class ProviderLimiter:
    def __init__(
        self, sessionFactory: SessionFactory, clock: Clock, provider: str, limit: int, leaseS: float
    ) -> None:
        if limit < 1:
            raise ValueError("the provider limit must be at least 1")
        self.sessionFactory = sessionFactory
        self.clock = clock
        self.provider = provider
        self.limit = limit
        self.lease = timedelta(seconds=leaseS)
        self.slotsReady = False

    def ensureSlots(self) -> None:
        """Slots 0..limit-1 exist. Slots above a lowered limit stay in the table and are not used."""
        if self.slotsReady:
            return
        rows = [{"provider": self.provider, "slotNo": slotNo} for slotNo in range(self.limit)]
        with self.sessionFactory() as session, session.begin():
            session.execute(insert(ProviderSlotRecord).values(rows).on_conflict_do_nothing())
        self.slotsReady = True

    def tryAcquire(self, holderRequestId: str) -> Slot | Wait:
        self.ensureSlots()
        now = self.clock.now()
        query = (
            select(ProviderSlotRecord)
            .where(ProviderSlotRecord.provider == self.provider, ProviderSlotRecord.slotNo < self.limit)
            .order_by(ProviderSlotRecord.slotNo)
            .with_for_update()
        )
        with self.sessionFactory() as session, session.begin():
            slots = list(session.scalars(query))
            notBefore = max((slot.notBefore for slot in slots if slot.notBefore is not None), default=None)
            if notBefore is not None and notBefore > now:
                return Wait((notBefore - now).total_seconds(), "backoff")
            free = [slot for slot in slots if slot.leaseUntil is None or slot.leaseUntil <= now]
            if not free:
                ends = [slot.leaseUntil for slot in slots if slot.leaseUntil is not None]
                return Wait((min(ends) - now).total_seconds(), "busy")
            chosen = free[0]
            chosen.leaseToken = uuid.uuid4().hex
            chosen.leaseUntil = now + self.lease
            chosen.holderRequestId = holderRequestId
            return Slot(self.provider, chosen.slotNo, chosen.leaseToken)

    def release(self, slot: Slot, backoffS: float | None = None) -> None:
        """Free our slot (only if it is still ours). With backoffS, nobody calls the provider before then."""
        with self.sessionFactory() as session, session.begin():
            session.execute(
                update(ProviderSlotRecord)
                .where(
                    ProviderSlotRecord.provider == slot.provider,
                    ProviderSlotRecord.slotNo == slot.slotNo,
                    ProviderSlotRecord.leaseToken == slot.leaseToken,
                )
                .values(leaseToken=None, leaseUntil=None, holderRequestId=None)
            )
            if backoffS is not None and backoffS > 0:
                until = self.clock.now() + timedelta(seconds=backoffS)
                session.execute(
                    update(ProviderSlotRecord)
                    .where(
                        ProviderSlotRecord.provider == slot.provider,
                        or_(ProviderSlotRecord.notBefore.is_(None), ProviderSlotRecord.notBefore < until),
                    )
                    .values(notBefore=until)
                )


class LimitedTransport:
    def __init__(self, inner: ModelTransport, limiter: ProviderLimiter, clock: Clock, policy: RetryPolicy) -> None:
        self.inner = inner
        self.limiter = limiter
        self.clock = clock
        self.policy = policy

    @property
    def name(self) -> TransportName:
        return self.inner.name

    def complete(self, call: ModelCall) -> ModelReply:
        deadline = self.clock.now() + timedelta(seconds=self.policy.maxWaitS)
        if call.deadline is not None:
            deadline = min(deadline, call.deadline)
        attempt = 0
        while True:
            slot = self.waitForSlot(call, deadline)
            attempt += 1
            try:
                reply = self.inner.complete(call)
            except TransportError as error:
                if error.code not in REPEATED_CODES:
                    self.limiter.release(slot)
                    raise
                backoffS = float(error.retryAfterS) if error.retryAfterS is not None else self.policy.defaultBackoffS
                self.limiter.release(slot, backoffS)
                retryAfterS = max(1, math.ceil(backoffS))
                if attempt >= self.policy.maxAttempts:
                    raise TransportError(
                        error.code, f"{error.message}; gave up after {attempt} attempts", retryAfterS
                    ) from error
                if self.clock.now() + timedelta(seconds=backoffS) > deadline:
                    raise TransportError(
                        error.code, f"{error.message}; the pause is longer than the wait budget", retryAfterS
                    ) from error
                continue
            except BaseException:
                self.limiter.release(slot)
                raise
            self.limiter.release(slot)
            return reply

    def waitForSlot(self, call: ModelCall, deadline: datetime) -> Slot:
        while True:
            outcome = self.limiter.tryAcquire(call.requestId)
            if isinstance(outcome, Slot):
                return outcome
            pauseS = outcome.seconds if outcome.reason == "backoff" else min(outcome.seconds, self.policy.pollS)
            pauseS = max(pauseS, 0.0)
            if self.clock.now() + timedelta(seconds=pauseS) > deadline:
                raise TransportError(
                    "rate_limited",
                    "every provider slot is busy or paused; repeat with the same requestId",
                    max(1, math.ceil(outcome.seconds)),
                )
            self.clock.sleep(pauseS)
