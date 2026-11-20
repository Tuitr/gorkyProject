"""Index the outbox entries still eligible for delivery."""

import sqlalchemy as sa
from alembic import op

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_outbox_pending_sequence", "outbox", ["sequence"],
        schema="cabinet", postgresql_where=sa.text("published_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_pending_sequence", table_name="outbox", schema="cabinet")
