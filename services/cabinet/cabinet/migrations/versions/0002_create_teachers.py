"""create teachers and login throttle

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teachers",
        sa.Column("teacher_id", sa.String(length=36), nullable=False),
        sa.Column("login", sa.String(length=254), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("password_hash LIKE '$argon2id$%'", name="password_hash_is_argon2id"),
        sa.PrimaryKeyConstraint("teacher_id"),
        sa.UniqueConstraint("login"),
        schema="cabinet",
    )
    op.create_table(
        "login_throttle",
        sa.Column("throttle_key", sa.String(length=80), nullable=False),
        sa.Column("failures", sa.Integer(), nullable=False),
        sa.Column("window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("failures >= 0", name="failures_not_negative"),
        sa.PrimaryKeyConstraint("throttle_key"),
        schema="cabinet",
    )


def downgrade() -> None:
    op.drop_table("login_throttle", schema="cabinet")
    op.drop_table("teachers", schema="cabinet")
