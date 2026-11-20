"""Save cabinet events and enqueue the existing variants and checks once."""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("sequence", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column("event_id", sa.String(36), nullable=False, unique=True),
        sa.Column("data", JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True)),
        schema="cabinet",
    )
    # Old requests have no saved trace ID or event timestamp. Enqueue them with migration-time metadata.
    op.execute("""
        WITH events AS (
            SELECT gen_random_uuid()::text AS event_id, assignment_id, assignment_rev,
                   jsonb_build_object(
                       'assignmentId', assignment_id, 'assignmentRev', assignment_rev,
                       'docRef', jsonb_build_object('bucket', bucket, 'key', object_key, 'sha256', sha256),
                       'requirements', requirements
                   ) AS payload
            FROM cabinet.assignments
        )
        INSERT INTO cabinet.outbox (event_id, data, occurred_at)
        SELECT event_id, jsonb_build_object(
            'v', 1, 'eventId', event_id, 'traceId', event_id, 'type', 'assignment.published',
            'occurredAt', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            'payload', payload
        ), now()
        FROM events
        ORDER BY assignment_id, assignment_rev
    """)
    op.execute("""
        WITH events AS (
            SELECT gen_random_uuid()::text AS event_id, c.check_id,
                   jsonb_build_object(
                       'assignmentId', c.assignment_id, 'assignmentRev', c.assignment_rev,
                       'studentId', a.student_id, 'attemptId', a.attempt_id, 'checkId', c.check_id,
                       'docRef', jsonb_build_object('bucket', a.bucket, 'key', a.object_key, 'sha256', a.sha256)
                   ) AS payload
            FROM cabinet.attempt_checks c
            JOIN cabinet.attempts a ON a.attempt_id = c.attempt_id
        )
        INSERT INTO cabinet.outbox (event_id, data, occurred_at)
        SELECT event_id, jsonb_build_object(
            'v', 1, 'eventId', event_id, 'traceId', event_id, 'type', 'attempt.submitted',
            'occurredAt', to_char(now() AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'),
            'payload', payload
        ), now()
        FROM events
        ORDER BY check_id
    """)


def downgrade() -> None:
    op.drop_table("outbox", schema="cabinet")
