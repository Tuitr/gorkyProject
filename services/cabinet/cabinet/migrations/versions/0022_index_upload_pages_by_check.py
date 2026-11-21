"""Index the photos and upload time of a check for reads from the gradebook."""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_cabinet_upload_files_check_id", "upload_files", ["check_id"], schema="cabinet")


def downgrade() -> None:
    op.drop_index("ix_cabinet_upload_files_check_id", table_name="upload_files", schema="cabinet")
