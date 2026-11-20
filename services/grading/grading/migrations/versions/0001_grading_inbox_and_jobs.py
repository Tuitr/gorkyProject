"""Create the grading inbox and job tables.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assignment_jobs",
        sa.Column("assignment_id", sa.Text(), primary_key=True),
        sa.Column("assignment_rev", sa.BigInteger(), primary_key=True),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="queued"),
        sa.CheckConstraint("assignment_rev >= 1", name="assignment_jobs_positive_rev"),
        schema="grading",
    )
    op.create_table(
        "check_jobs",
        sa.Column("check_id", sa.Text(), primary_key=True),
        sa.Column("assignment_id", sa.Text(), nullable=False),
        sa.Column("assignment_rev", sa.BigInteger(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column("state", sa.Text(), nullable=False, server_default="queued"),
        sa.CheckConstraint("assignment_rev >= 1", name="check_jobs_positive_rev"),
        schema="grading",
    )
    op.create_table(
        "inbox",
        sa.Column("event_id", sa.Text(), primary_key=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("trace_id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("check_id", sa.Text()),
        sa.Column("assignment_id", sa.Text()),
        sa.Column("assignment_rev", sa.BigInteger()),
        sa.ForeignKeyConstraint(
            ["check_id"], ["grading.check_jobs.check_id"],
            name="inbox_check_job", deferrable=True, initially="DEFERRED",
        ),
        sa.ForeignKeyConstraint(
            ["assignment_id", "assignment_rev"],
            ["grading.assignment_jobs.assignment_id", "grading.assignment_jobs.assignment_rev"],
            name="inbox_assignment_job", deferrable=True, initially="DEFERRED",
        ),
        sa.CheckConstraint(
            "(event_type = 'attempt.submitted' AND check_id IS NOT NULL"
            " AND assignment_id IS NULL AND assignment_rev IS NULL) OR"
            " (event_type = 'assignment.published' AND check_id IS NULL"
            " AND assignment_id IS NOT NULL AND assignment_rev IS NOT NULL)",
            name="inbox_one_job",
        ),
        schema="grading",
    )


def downgrade() -> None:
    op.drop_table("inbox", schema="grading")
    op.drop_table("check_jobs", schema="grading")
    op.drop_table("assignment_jobs", schema="grading")
