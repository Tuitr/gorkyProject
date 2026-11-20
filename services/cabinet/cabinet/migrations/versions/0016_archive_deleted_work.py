"""Mark removed assignments and submissions while preserving their history."""

import sqlalchemy as sa
from alembic import op

revision = "0016"
down_revision = "0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("assignments", sa.Column("deleted_at", sa.DateTime(timezone=True)), schema="cabinet")
    op.add_column("attempts", sa.Column("deleted_at", sa.DateTime(timezone=True)), schema="cabinet")
    op.drop_constraint("uq_attempts_submission", "attempts", schema="cabinet", type_="unique")
    op.create_index(
        "uq_attempts_submission", "attempts", ["assignment_id", "student_id", "sha256"],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"), schema="cabinet",
    )


def downgrade() -> None:
    op.drop_index("uq_attempts_submission", table_name="attempts", schema="cabinet")
    op.create_unique_constraint("uq_attempts_submission", "attempts", ["assignment_id", "student_id", "sha256"], schema="cabinet")
    op.drop_column("attempts", "deleted_at", schema="cabinet")
    op.drop_column("assignments", "deleted_at", schema="cabinet")
