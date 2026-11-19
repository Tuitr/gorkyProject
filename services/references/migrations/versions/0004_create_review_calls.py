"""create review call journal

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "review_calls",
        sa.Column("request_id", sa.String(length=128), nullable=False),
        sa.Column("body_fingerprint", sa.String(length=80), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("state IN ('pending', 'succeeded')", name="review_state_known"),
        sa.CheckConstraint(
            "state <> 'succeeded' OR (response IS NOT NULL AND lease_token IS NULL)", name="succeeded_has_response"
        ),
        sa.CheckConstraint("attempts >= 0", name="attempts_not_negative"),
        sa.PrimaryKeyConstraint("request_id"),
        schema="refs",
    )


def downgrade() -> None:
    op.drop_table("review_calls", schema="refs")
