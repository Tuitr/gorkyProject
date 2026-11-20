"""Tables of teacher login in the `cabinet` schema."""

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from cabinet.database import Base


class TeacherRecord(Base):
    """A teacher account. password_hash is an Argon2id string with its own random salt and parameters."""

    __tablename__ = "teachers"
    __table_args__ = (CheckConstraint("password_hash LIKE '$argon2id$%'", name="password_hash_is_argon2id"),)

    teacherId: Mapped[str] = mapped_column("teacher_id", String(36), primary_key=True)
    login: Mapped[str] = mapped_column("login", String(254), unique=True)
    """Normalized: no surrounding spaces, case folded."""
    displayName: Mapped[str] = mapped_column("display_name", String(200))
    passwordHash: Mapped[str] = mapped_column("password_hash", String(255))
    createdAt: Mapped[datetime] = mapped_column("created_at", DateTime(timezone=True), server_default=func.now())


class LoginThrottleRecord(Base):
    """Failed logins per key: "login:<sha256 of the login>" or "client:<sha256 of the address>".

    Keys are hashes, so the table keeps neither logins people mistyped nor client addresses.
    """

    __tablename__ = "login_throttle"
    __table_args__ = (CheckConstraint("failures >= 0", name="failures_not_negative"),)

    throttleKey: Mapped[str] = mapped_column("throttle_key", String(80), primary_key=True)
    failures: Mapped[int] = mapped_column("failures", Integer)
    windowStart: Mapped[datetime] = mapped_column("window_start", DateTime(timezone=True))
    blockedUntil: Mapped[datetime | None] = mapped_column("blocked_until", DateTime(timezone=True))
