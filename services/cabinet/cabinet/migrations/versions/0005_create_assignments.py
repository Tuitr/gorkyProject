"""Store immutable assignment revisions."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assignments",
        sa.Column("assignment_id", sa.String(36), primary_key=True),
        sa.Column("assignment_rev", sa.Integer(), primary_key=True, autoincrement=False),
        sa.Column("class_id", sa.String(36), nullable=False),
        sa.Column("assignment_date", sa.Date(), nullable=False),
        sa.Column("requirements", JSONB(), nullable=False),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["cabinet.classes.class_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("object_key"),
        schema="cabinet",
    )
    op.create_index("ix_cabinet_assignments_class_id", "assignments", ["class_id"], schema="cabinet")


def downgrade() -> None:
    op.drop_table("assignments", schema="cabinet")
