"""create reference registry

Revision ID: 0001
Revises:
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('reference_records',
    sa.Column('reference_id', sa.String(length=64), nullable=False),
    sa.Column('assignment_id', sa.String(length=128), nullable=False),
    sa.Column('assignment_rev', sa.Integer(), nullable=False),
    sa.Column('task_id', sa.String(length=128), nullable=False),
    sa.Column('statement', sa.Text(), nullable=False),
    sa.Column('constraints', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
    sa.Column('family_hint', sa.String(length=128), nullable=True),
    sa.Column('state', sa.String(length=32), nullable=False),
    sa.Column('source', sa.String(length=32), nullable=True),
    sa.Column('solver_version', sa.String(length=128), nullable=True),
    sa.Column('schema_id', sa.String(length=64), nullable=True),
    sa.Column('value', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    sa.Column('reason_code', sa.String(length=64), nullable=True),
    sa.Column('ready_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("source IS NULL OR source = 'fixture' OR solver_version IS NOT NULL", name='non_fixture_has_solver_version'),
    sa.CheckConstraint("state <> 'ready' OR (schema_id IS NOT NULL AND source IS NOT NULL AND value IS NOT NULL AND reason_code IS NULL)", name='ready_has_value'),
    sa.CheckConstraint("state IN ('ready', 'pending', 'unsupported', 'validation_failed')", name='state_known'),
    sa.CheckConstraint("state NOT IN ('unsupported', 'validation_failed') OR (reason_code IS NOT NULL AND value IS NULL)", name='refusal_has_reason'),
    sa.CheckConstraint('assignment_rev >= 1', name='assignment_rev_positive'),
    sa.PrimaryKeyConstraint('reference_id'),
    schema='refs'
    )


def downgrade() -> None:
    op.drop_table('reference_records', schema='refs')
