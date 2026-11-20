"""Store the school subject of an assignment revision: math or physics."""

import sqlalchemy as sa
from alembic import op

revision = "0017"
down_revision = "0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "assignments",
        sa.Column("subject", sa.String(16), nullable=False, server_default="math"),
        schema="cabinet",
    )
    op.create_check_constraint(
        "ck_assignments_subject", "assignments", "subject IN ('math', 'physics')", schema="cabinet",
    )


def downgrade() -> None:
    op.drop_constraint("ck_assignments_subject", "assignments", schema="cabinet", type_="check")
    op.drop_column("assignments", "subject", schema="cabinet")
