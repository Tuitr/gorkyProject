"""Record submission uploads and their original files."""

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "uploads",
        sa.Column("upload_id", sa.String(36), primary_key=True),
        sa.Column("assignment_id", sa.String(36), nullable=False),
        sa.Column("assignment_rev", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["assignment_id", "assignment_rev"],
            ["cabinet.assignments.assignment_id", "cabinet.assignments.assignment_rev"],
        ),
        schema="cabinet",
    )
    op.create_table(
        "upload_files",
        sa.Column("file_id", sa.String(36), primary_key=True),
        sa.Column("upload_id", sa.String(36), nullable=False),
        sa.Column("original_name", sa.String(255), nullable=False),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.ForeignKeyConstraint(["upload_id"], ["cabinet.uploads.upload_id"], ondelete="CASCADE"),
        sa.UniqueConstraint("object_key"),
        schema="cabinet",
    )
    op.create_index("ix_cabinet_upload_files_upload_id", "upload_files", ["upload_id"], schema="cabinet")


def downgrade() -> None:
    op.drop_table("upload_files", schema="cabinet")
    op.drop_table("uploads", schema="cabinet")
