"""One local record per requestId, so a repeated review call never asks the model twice for one answer.

- same requestId and same body after success: the stored response, no model call;
- same requestId and another body: 409 conflict, always;
- same request while another worker is still calling the model: 503 unavailable with Retry-After
  (the caller repeats with the same requestId, as for any retryable error);
- the running worker failed or its lease expired (crash, lost connection): the next repeat takes over
  and calls the model again with the same requestId.
After a network timeout the provider may still have answered, so one external call cannot be promised.
What is promised: one final record, and the first valid answer stored is the one every repeat gets.
"""

import hashlib
import math
import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from contracts.v1.references.modelreview import ModelReviewRequest, ModelReviewResponse
from references.gateway.tables import ReviewCallRecord
from references.gateway.transports.base import GatewayError, RequestRefused
from references.registry.keys import canonicalJson
from references.registry.store import SessionFactory
from shared.clock import Clock

FINGERPRINT_VERSION = "b1"


def bodyFingerprint(request: ModelReviewRequest) -> str:
    canonical = canonicalJson(request.model_dump(mode="json"))
    return f"{FINGERPRINT_VERSION}:{hashlib.sha256(canonical.encode('utf-8')).hexdigest()}"


@dataclass(frozen=True)
class Claim:
    """The right to call the model for this requestId until the lease ends."""

    requestId: str
    leaseToken: str


def checkSameBody(record: ReviewCallRecord, fingerprint: str) -> None:
    if record.bodyFingerprint != fingerprint:
        raise RequestRefused("conflict", "requestId was already used with a different body")


class ReviewJournal:
    def __init__(self, sessionFactory: SessionFactory, clock: Clock, leaseS: float) -> None:
        self.sessionFactory = sessionFactory
        self.clock = clock
        self.lease = timedelta(seconds=leaseS)

    def stored(self, request: ModelReviewRequest) -> ModelReviewResponse | None:
        """Read before any other work: the stored answer or None. Another body with this requestId is 409."""
        record = self.read(request.requestId)
        if record is None:
            return None
        checkSameBody(record, bodyFingerprint(request))
        return ModelReviewResponse.model_validate(record.response) if record.state == "succeeded" else None

    def begin(self, request: ModelReviewRequest) -> Claim | ModelReviewResponse:
        """The stored response, or a claim to call the model. Raises conflict or still-running errors."""
        fingerprint = bodyFingerprint(request)
        now = self.clock.now()
        token = uuid.uuid4().hex
        statement = (
            insert(ReviewCallRecord)
            .values(
                requestId=request.requestId,
                bodyFingerprint=fingerprint,
                state="pending",
                leaseToken=token,
                leaseUntil=now + self.lease,
                attempts=1,
            )
            .on_conflict_do_nothing(index_elements=["request_id"])
            .returning(ReviewCallRecord.requestId)
        )
        with self.sessionFactory() as session, session.begin():
            if session.execute(statement).first() is not None:
                return Claim(request.requestId, token)
            record = session.get(ReviewCallRecord, request.requestId, with_for_update=True)
            assert record is not None
            checkSameBody(record, fingerprint)
            if record.state == "succeeded":
                return ModelReviewResponse.model_validate(record.response)
            if record.leaseUntil is not None and record.leaseUntil > now:
                waitS = max(1, math.ceil((record.leaseUntil - now).total_seconds()))
                raise GatewayError(
                    "unavailable", "the same request is still being reviewed; repeat it with the same requestId", waitS
                )
            record.leaseToken = token
            record.leaseUntil = now + self.lease
            record.attempts += 1
            record.lastErrorCode = None
        return Claim(request.requestId, token)

    def succeed(self, claim: Claim, response: ModelReviewResponse) -> ModelReviewResponse:
        """Store the first valid answer. If another worker stored one already, that one wins."""
        with self.sessionFactory() as session, session.begin():
            record = session.get(ReviewCallRecord, claim.requestId, with_for_update=True)
            assert record is not None
            if record.state == "succeeded":
                return ModelReviewResponse.model_validate(record.response)
            record.state = "succeeded"
            record.response = response.model_dump(mode="json")
            record.leaseToken = None
            record.leaseUntil = None
            record.lastErrorCode = None
        return response

    def fail(self, claim: Claim, code: str) -> None:
        """Release the lease so a repeat may try again. A worker whose lease was taken over changes nothing."""
        statement = (
            update(ReviewCallRecord)
            .where(
                ReviewCallRecord.requestId == claim.requestId,
                ReviewCallRecord.state == "pending",
                ReviewCallRecord.leaseToken == claim.leaseToken,
            )
            .values(leaseToken=None, leaseUntil=None, lastErrorCode=code)
        )
        with self.sessionFactory() as session, session.begin():
            session.execute(statement)

    def read(self, requestId: str) -> ReviewCallRecord | None:
        with self.sessionFactory() as session:
            record = session.scalars(select(ReviewCallRecord).where(ReviewCallRecord.requestId == requestId)).first()
            if record is not None:
                session.expunge(record)
            return record
