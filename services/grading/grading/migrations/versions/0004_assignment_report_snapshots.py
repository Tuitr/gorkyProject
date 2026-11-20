"""Pin class report snapshots and record teacher acceptance.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("check_result_revisions", sa.Column("accepted_at", sa.DateTime(timezone=True)), schema="grading")
    op.create_index("check_jobs_assignment_revision", "check_jobs", ["assignment_id", "assignment_rev"], schema="grading")
    op.create_index("inbox_check_time", "inbox", ["check_id", "occurred_at"], schema="grading")
    op.create_table(
        "assignment_report_snapshots",
        sa.Column("assignment_id", sa.Text(), primary_key=True),
        sa.Column("rev", sa.BigInteger(), primary_key=True),
        sa.Column("assignment_rev", sa.BigInteger(), nullable=False),
        sa.Column("report", JSONB(), nullable=False),
        sa.CheckConstraint("rev >= 1", name="assignment_report_snapshots_positive_rev"),
        sa.ForeignKeyConstraint(
            ["assignment_id", "assignment_rev"], ["grading.assignment_jobs.assignment_id", "grading.assignment_jobs.assignment_rev"],
        ),
        schema="grading",
    )


def downgrade() -> None:
    op.drop_table("assignment_report_snapshots", schema="grading")
    op.drop_index("inbox_check_time", table_name="inbox", schema="grading")
    op.drop_index("check_jobs_assignment_revision", table_name="check_jobs", schema="grading")
    op.drop_column("check_result_revisions", "accepted_at", schema="grading")
