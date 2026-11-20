"""Record the teacher who first published each result revision.

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("check_result_revisions", sa.Column("accepted_by", sa.Text()), schema="grading")


def downgrade() -> None:
    op.drop_column("check_result_revisions", "accepted_by", schema="grading")
