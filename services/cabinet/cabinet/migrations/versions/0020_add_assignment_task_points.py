"""Store teacher-specified maximum scores on each assignment revision."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0020"
down_revision = "0019"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assignments", sa.Column("task_points", JSONB, nullable=False, server_default="[]"), schema="cabinet",
    )


def downgrade() -> None:
    op.drop_column("assignments", "task_points", schema="cabinet")
