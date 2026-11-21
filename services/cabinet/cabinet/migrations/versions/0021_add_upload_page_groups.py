"""Group uploaded pages and identify a submission by all its original bytes in order."""

import sqlalchemy as sa
from alembic import op

revision = "0021"
down_revision = "0020"
branch_labels = None
depends_on = None


def useAttemptHash(column: str) -> None:
    op.drop_index("uq_attempts_submission", table_name="attempts", schema="cabinet")
    op.create_index(
        "uq_attempts_submission", "attempts", ["assignment_id", "student_id", column],
        unique=True, postgresql_where=sa.text("deleted_at IS NULL"), schema="cabinet",
    )


def upgrade() -> None:
    op.add_column("attempts", sa.Column("submission_hash", sa.String(64)), schema="cabinet")
    op.execute("UPDATE cabinet.attempts SET submission_hash = sha256")
    op.alter_column("attempts", "submission_hash", nullable=False, schema="cabinet")
    useAttemptHash("submission_hash")
    op.add_column("upload_files", sa.Column("group_id", sa.String(36)), schema="cabinet")
    op.add_column("upload_files", sa.Column("page_index", sa.Integer()), schema="cabinet")


def downgrade() -> None:
    useAttemptHash("sha256")
    op.drop_column("attempts", "submission_hash", schema="cabinet")
    op.drop_column("upload_files", "page_index", schema="cabinet")
    op.drop_column("upload_files", "group_id", schema="cabinet")
