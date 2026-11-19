"""create provider slots

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_slots",
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("slot_no", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("holder_request_id", sa.String(length=128), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("slot_no >= 0", name="slot_no_not_negative"),
        sa.PrimaryKeyConstraint("provider", "slot_no"),
        schema="refs",
    )


def downgrade() -> None:
    op.drop_table("provider_slots", schema="refs")
