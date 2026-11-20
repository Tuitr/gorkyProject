"""Limit of failed logins in PostgreSQL, shared by every cabinet process.

A login is blocked for windowS after maxFailures failures within windowS; a client address after
clientMaxFailures failures of any logins. While blocked, even the right password gets 429, so the answer
tells nothing about the password. A successful login clears the failures of its login, not of the address.
"""

import hashlib
import math
from collections.abc import Sequence
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from cabinet.auth.tables import LoginThrottleRecord
from shared.clock import Clock


def loginKey(login: str) -> str:
    return "login:" + hashlib.sha256(login.encode()).hexdigest()


def clientKey(address: str) -> str:
    return "client:" + hashlib.sha256(address.encode()).hexdigest()


class LoginThrottle:
    def __init__(
        self,
        sessionFactory: sessionmaker[Session],
        clock: Clock,
        *,
        maxFailures: int,
        clientMaxFailures: int,
        windowS: int,
    ) -> None:
        self.sessionFactory = sessionFactory
        self.clock = clock
        self.maxFailures = maxFailures
        self.clientMaxFailures = clientMaxFailures
        self.window = timedelta(seconds=windowS)

    def limitOf(self, key: str) -> int:
        return self.clientMaxFailures if key.startswith("client:") else self.maxFailures

    def retryAfterS(self, keys: Sequence[str]) -> int | None:
        """Seconds until every key may try again, or None when none is blocked."""
        now = self.clock.now()
        with self.sessionFactory() as session:
            rows = session.scalars(select(LoginThrottleRecord).where(LoginThrottleRecord.throttleKey.in_(keys)))
            waits = [(row.blockedUntil - now).total_seconds() for row in rows if row.blockedUntil is not None]
        longest = max((wait for wait in waits if wait > 0), default=None)
        return None if longest is None else max(1, math.ceil(longest))

    def recordFailure(self, keys: Sequence[str]) -> None:
        now = self.clock.now()
        with self.sessionFactory.begin() as session:
            for key in sorted(keys):  # one lock order for every process
                session.execute(
                    insert(LoginThrottleRecord)
                    .values(throttleKey=key, failures=0, windowStart=now, blockedUntil=None)
                    .on_conflict_do_nothing(index_elements=["throttle_key"])
                )
                row = session.scalars(
                    select(LoginThrottleRecord).where(LoginThrottleRecord.throttleKey == key).with_for_update()
                ).one()
                if now - row.windowStart >= self.window:
                    row.failures, row.windowStart, row.blockedUntil = 0, now, None
                row.failures += 1
                if row.failures >= self.limitOf(key):
                    row.blockedUntil = now + self.window

    def clear(self, key: str) -> None:
        with self.sessionFactory.begin() as session:
            row = session.get(LoginThrottleRecord, key)
            if row is not None:
                session.delete(row)
