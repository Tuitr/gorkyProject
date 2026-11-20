"""Store deduplicated attempts and revision-specific checks."""

import sqlalchemy as sa
from alembic import op

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("uploads", sa.Column("uploaded_at", sa.DateTime(timezone=True)), schema="cabinet")
    op.create_table(
        "attempts",
        sa.Column("attempt_id", sa.String(36), primary_key=True),
        sa.Column("assignment_id", sa.String(36), nullable=False),
        sa.Column("student_id", sa.String(36), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("bucket", sa.String(63), nullable=False),
        sa.Column("object_key", sa.String(255), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["cabinet.students.student_id"]),
        sa.UniqueConstraint("assignment_id", "student_id", "sha256", name="uq_attempts_submission"),
        schema="cabinet",
    )
    op.create_table(
        "attempt_checks",
        sa.Column("check_id", sa.String(36), primary_key=True),
        sa.Column("attempt_id", sa.String(36), nullable=False),
        sa.Column("assignment_id", sa.String(36), nullable=False),
        sa.Column("assignment_rev", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["attempt_id"], ["cabinet.attempts.attempt_id"]),
        sa.ForeignKeyConstraint(["assignment_id", "assignment_rev"],
                                ["cabinet.assignments.assignment_id", "cabinet.assignments.assignment_rev"]),
        sa.UniqueConstraint("attempt_id", "assignment_rev", name="uq_attempt_checks_revision"),
        schema="cabinet",
    )
    op.create_foreign_key(
        "fk_upload_files_attempt", "upload_files", "attempts", ["attempt_id"], ["attempt_id"],
        source_schema="cabinet", referent_schema="cabinet",
    )
    op.create_foreign_key(
        "fk_upload_files_check", "upload_files", "attempt_checks", ["check_id"], ["check_id"],
        source_schema="cabinet", referent_schema="cabinet",
    )


def downgrade() -> None:
    op.drop_constraint("fk_upload_files_check", "upload_files", schema="cabinet")
    op.drop_constraint("fk_upload_files_attempt", "upload_files", schema="cabinet")
    op.execute("UPDATE cabinet.upload_files SET status = 'matched', attempt_id = NULL, check_id = NULL WHERE status = 'bound'")
    op.drop_table("attempt_checks", schema="cabinet")
    op.drop_table("attempts", schema="cabinet")
    op.drop_column("uploads", "uploaded_at", schema="cabinet")
