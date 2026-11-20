"""Store server-side teacher sessions."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sessions",
        sa.Column("token_hash", sa.String(64), primary_key=True),
        sa.Column("teacher_id", sa.String(36), sa.ForeignKey("cabinet.teachers.teacher_id", ondelete="CASCADE"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        schema="cabinet",
    )


def downgrade() -> None:
    op.drop_table("sessions", schema="cabinet")
