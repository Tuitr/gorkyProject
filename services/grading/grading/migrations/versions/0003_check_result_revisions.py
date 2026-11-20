"""Store check results and immutable revisions.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "check_result_revisions",
        sa.Column("check_id", sa.Text(), primary_key=True),
        sa.Column("result_rev", sa.BigInteger(), primary_key=True),
        sa.Column("calculation_id", sa.Text(), nullable=False),
        sa.Column("outcome", JSONB(), nullable=False),
        sa.Column("markdown_ref", JSONB(none_as_null=True)),
        sa.CheckConstraint("result_rev >= 1", name="check_result_revisions_positive_rev"),
        sa.UniqueConstraint("check_id", "calculation_id", name="check_result_revisions_calculation"),
        sa.ForeignKeyConstraint(["check_id"], ["grading.check_jobs.check_id"]),
        schema="grading",
    )


def downgrade() -> None:
    op.drop_table("check_result_revisions", schema="grading")
