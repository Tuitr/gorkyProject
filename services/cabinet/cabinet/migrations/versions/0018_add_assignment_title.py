"""Store the teacher-given title of an assignment revision, for example «Тепловые явления»."""

import sqlalchemy as sa
from alembic import op

revision = "0018"
down_revision = "0017"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assignments", sa.Column("title", sa.String(120), nullable=True), schema="cabinet")


def downgrade() -> None:
    op.drop_column("assignments", "title", schema="cabinet")
