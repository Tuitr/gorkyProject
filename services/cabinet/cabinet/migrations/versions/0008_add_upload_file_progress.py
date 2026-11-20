"""Store binding progress for each uploaded file."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_files",
        sa.Column("candidate_student_ids", ARRAY(sa.String(36)), server_default="{}", nullable=False),
        schema="cabinet",
    )
    op.add_column("upload_files", sa.Column("reason", sa.String(255)), schema="cabinet")
    op.add_column("upload_files", sa.Column("student_id", sa.String(36)), schema="cabinet")
    op.add_column("upload_files", sa.Column("attempt_id", sa.String(36)), schema="cabinet")
    op.add_column("upload_files", sa.Column("check_id", sa.String(36)), schema="cabinet")
    op.create_foreign_key(
        "fk_upload_files_student", "upload_files", "students", ["student_id"], ["student_id"],
        source_schema="cabinet", referent_schema="cabinet",
    )
    op.create_check_constraint(
        "ck_upload_files_status", "upload_files",
        "status IN ('pending_binding', 'needs_confirmation', 'bound', 'rejected')", schema="cabinet",
    )
    op.create_check_constraint(
        "ck_upload_files_attempt", "upload_files",
        "(status = 'bound' AND student_id IS NOT NULL AND attempt_id IS NOT NULL AND check_id IS NOT NULL) "
        "OR (status <> 'bound' AND attempt_id IS NULL AND check_id IS NULL)",
        schema="cabinet",
    )


def downgrade() -> None:
    op.drop_constraint("ck_upload_files_attempt", "upload_files", schema="cabinet")
    op.drop_constraint("ck_upload_files_status", "upload_files", schema="cabinet")
    op.drop_constraint("fk_upload_files_student", "upload_files", schema="cabinet")
    for name in ("check_id", "attempt_id", "student_id", "reason", "candidate_student_ids"):
        op.drop_column("upload_files", name, schema="cabinet")
