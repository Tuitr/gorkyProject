"""Persist teacher edit requests and their revision receipts.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teacher_review_edits",
        sa.Column("check_id", sa.Text(), primary_key=True),
        sa.Column("edit_id", sa.Text(), primary_key=True),
        sa.Column("result_rev", sa.BigInteger(), nullable=False),
        sa.Column("request", JSONB(), nullable=False),
        sa.ForeignKeyConstraint(
            ["check_id", "result_rev"], ["grading.check_result_revisions.check_id", "grading.check_result_revisions.result_rev"],
        ),
        schema="grading",
    )


def downgrade() -> None:
    op.drop_table("teacher_review_edits", schema="grading")
