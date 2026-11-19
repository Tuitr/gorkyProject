"""Journal of model review calls in refs.review_calls: one row per requestId.

A row is pending until a valid answer is stored; then it is succeeded and never changes again.
While a call runs, lease_token and lease_until say who runs it and until when.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from references.database import Base


class ReviewCallRecord(Base):
    __tablename__ = "review_calls"
    __table_args__ = (
        CheckConstraint("state IN ('pending', 'succeeded')", name="review_state_known"),
        CheckConstraint(
            "state <> 'succeeded' OR (response IS NOT NULL AND lease_token IS NULL)", name="succeeded_has_response"
        ),
        CheckConstraint("attempts >= 0", name="attempts_not_negative"),
    )

    requestId: Mapped[str] = mapped_column("request_id", String(128), primary_key=True)
    bodyFingerprint: Mapped[str] = mapped_column("body_fingerprint", String(80))
    state: Mapped[str] = mapped_column("state", String(16))
    leaseToken: Mapped[str | None] = mapped_column("lease_token", String(64))
    leaseUntil: Mapped[datetime | None] = mapped_column("lease_until", DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column("attempts", Integer)
    lastErrorCode: Mapped[str | None] = mapped_column("last_error_code", String(64))
    response: Mapped[dict[str, Any] | None] = mapped_column("response", JSONB(none_as_null=True))
    createdAt: Mapped[datetime] = mapped_column("created_at", DateTime(timezone=True), server_default=func.now())
    updatedAt: Mapped[datetime] = mapped_column(
        "updated_at", DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProviderSlotRecord(Base):
    """One row per allowed concurrent call to a provider. A call runs only while it holds a slot lease.

    not_before is shared by all slots of a provider: after 429/503 nobody calls before that time.
    """

    __tablename__ = "provider_slots"
    __table_args__ = (CheckConstraint("slot_no >= 0", name="slot_no_not_negative"),)

    provider: Mapped[str] = mapped_column("provider", String(32), primary_key=True)
    slotNo: Mapped[int] = mapped_column("slot_no", Integer, primary_key=True)
    leaseToken: Mapped[str | None] = mapped_column("lease_token", String(64))
    leaseUntil: Mapped[datetime | None] = mapped_column("lease_until", DateTime(timezone=True))
    holderRequestId: Mapped[str | None] = mapped_column("holder_request_id", String(128))
    notBefore: Mapped[datetime | None] = mapped_column("not_before", DateTime(timezone=True))
