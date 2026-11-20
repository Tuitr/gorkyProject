"""Record a matched student before creating a grading attempt."""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("ck_upload_files_status", "upload_files", schema="cabinet")
    op.create_check_constraint(
        "ck_upload_files_status", "upload_files",
        "status IN ('pending_binding', 'needs_confirmation', 'matched', 'bound', 'rejected')",
        schema="cabinet",
    )
    op.create_check_constraint(
        "ck_upload_files_match", "upload_files", "status <> 'matched' OR student_id IS NOT NULL",
        schema="cabinet",
    )


def downgrade() -> None:
    op.drop_constraint("ck_upload_files_match", "upload_files", schema="cabinet")
    op.drop_constraint("ck_upload_files_status", "upload_files", schema="cabinet")
    op.execute("UPDATE cabinet.upload_files SET status = 'pending_binding', student_id = NULL WHERE status = 'matched'")
    op.create_check_constraint(
        "ck_upload_files_status", "upload_files",
        "status IN ('pending_binding', 'needs_confirmation', 'bound', 'rejected')", schema="cabinet",
    )
