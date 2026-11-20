"""Track checkpoints, leases and retries for grading jobs.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

TABLES = ("assignment_jobs", "check_jobs")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(table, sa.Column("completed_stage", sa.Text()), schema="grading")
        op.add_column(table, sa.Column("stage_data", JSONB()), schema="grading")
        op.add_column(table, sa.Column("lease_until", sa.DateTime(timezone=True)), schema="grading")
        op.add_column(table, sa.Column("lease_version", sa.BigInteger(), nullable=False, server_default="0"), schema="grading")
        op.add_column(table, sa.Column("next_attempt_at", sa.DateTime(timezone=True)), schema="grading")
        op.create_check_constraint(
            f"{table}_known_state", table,
            "state IN ('queued', 'waiting_reference', 'running', 'ready', 'needs_review', 'failed')",
            schema="grading",
        )


def downgrade() -> None:
    for table in TABLES:
        op.drop_constraint(f"{table}_known_state", table, schema="grading")
        for column in ("next_attempt_at", "lease_version", "lease_until", "stage_data", "completed_stage"):
            op.drop_column(table, column, schema="grading")
