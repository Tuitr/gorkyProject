"""Distinguish confirmed bindings from automatic matches."""

import sqlalchemy as sa
from alembic import op

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("upload_files", sa.Column("binding_mode", sa.String(16)), schema="cabinet")
    op.create_check_constraint(
        "ck_upload_files_binding_mode", "upload_files",
        "binding_mode IS NULL OR (binding_mode IN ('selected', 'created') AND student_id IS NOT NULL)",
        schema="cabinet",
    )


def downgrade() -> None:
    op.drop_constraint("ck_upload_files_binding_mode", "upload_files", schema="cabinet")
    op.drop_column("upload_files", "binding_mode", schema="cabinet")
