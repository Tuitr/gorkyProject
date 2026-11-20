"""Keep historical work references when classes and students are removed."""

import sqlalchemy as sa
from alembic import op

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("classes", sa.Column("deleted_at", sa.DateTime(timezone=True)), schema="cabinet")
    op.add_column("students", sa.Column("deleted_at", sa.DateTime(timezone=True)), schema="cabinet")


def downgrade() -> None:
    op.drop_column("students", "deleted_at", schema="cabinet")
    op.drop_column("classes", "deleted_at", schema="cabinet")
