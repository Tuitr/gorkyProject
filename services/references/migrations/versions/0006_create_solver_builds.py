"""create solver builds and versions

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "solver_builds",
        sa.Column("build_id", sa.String(length=64), nullable=False),
        sa.Column("task_key", sa.String(length=80), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("constraints", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("reason_code", sa.String(length=64), nullable=True),
        sa.Column("model_calls", sa.Integer(), nullable=False),
        sa.Column("lease_token", sa.String(length=64), nullable=True),
        sa.Column("lease_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("not_before", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deadline", sa.DateTime(timezone=True), nullable=False),
        sa.Column("solver_version", sa.String(length=128), nullable=True),
        sa.Column("log", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("state IN ('queued', 'running', 'published', 'failed')", name="build_state_known"),
        sa.CheckConstraint("state <> 'published' OR solver_version IS NOT NULL", name="published_has_version"),
        sa.CheckConstraint("state <> 'failed' OR reason_code IS NOT NULL", name="failed_has_reason"),
        sa.CheckConstraint("state = 'running' OR lease_token IS NULL", name="only_running_has_lease"),
        sa.CheckConstraint("model_calls >= 0", name="model_calls_not_negative"),
        sa.PrimaryKeyConstraint("build_id"),
        sa.UniqueConstraint("task_key"),
        schema="refs",
    )
    op.create_index(
        "solver_builds_open",
        "solver_builds",
        ["not_before"],
        schema="refs",
        postgresql_where=sa.text("state IN ('queued', 'running')"),
    )
    op.create_table(
        "solver_versions",
        sa.Column("solver_version", sa.String(length=128), nullable=False),
        sa.Column("family_id", sa.String(length=64), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("task_key", sa.String(length=80), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("schema_id", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("code_sha256", sa.String(length=64), nullable=False),
        sa.Column("tests_sha256", sa.String(length=64), nullable=False),
        sa.Column("metadata_key", sa.String(length=512), nullable=False),
        sa.Column("withdrawn", sa.Boolean(), server_default="false", nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version_no >= 1", name="version_no_positive"),
        sa.CheckConstraint("source IN ('generated', 'manual')", name="version_source_known"),
        sa.PrimaryKeyConstraint("solver_version"),
        sa.UniqueConstraint("family_id", "version_no", name="solver_versions_family_no"),
        schema="refs",
    )
    op.create_index("ix_refs_solver_versions_task_key", "solver_versions", ["task_key"], schema="refs")


def downgrade() -> None:
    op.drop_index("ix_refs_solver_versions_task_key", table_name="solver_versions", schema="refs")
    op.drop_table("solver_versions", schema="refs")
    op.drop_index("solver_builds_open", table_name="solver_builds", schema="refs")
    op.drop_table("solver_builds", schema="refs")
