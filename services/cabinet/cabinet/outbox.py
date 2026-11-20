"""Save validated checks.v1 events in the cabinet transaction."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, Session, mapped_column

from cabinet.database import Base
from contracts.v1.events.checks import ChecksEvent


@dataclass(frozen=True)
class EventContext:
    traceId: str
    occurredAt: datetime


class OutboxRecord(Base):
    __tablename__ = "outbox"

    sequence: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    eventId: Mapped[str] = mapped_column("event_id", String(36), unique=True)
    data: Mapped[dict[str, object]] = mapped_column(JSONB)
    occurredAt: Mapped[datetime] = mapped_column("occurred_at", DateTime(timezone=True))
    publishedAt: Mapped[datetime | None] = mapped_column("published_at", DateTime(timezone=True))


def enqueueEvent(db: Session, event: ChecksEvent) -> None:
    db.add(OutboxRecord(eventId=event.eventId, data=event.model_dump(mode="json"), occurredAt=event.occurredAt))
