"""add lookup key

Revision ID: 0002
Revises: 0001

Existing rows get their key computed here with a frozen copy of the k1 rule
(references/registry/keys.py), so the migration does not depend on future app code.
"""

import hashlib
import json

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def lookupKeyV1(row: sa.Row[tuple[str, str, int, str, str, object]]) -> str:
    payload = {
        "assignmentId": row.assignment_id,
        "assignmentRev": row.assignment_rev,
        "taskId": row.task_id,
        "statement": row.statement,
        "constraints": row.constraints,
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "k1:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def upgrade() -> None:
    op.add_column("reference_records", sa.Column("lookup_key", sa.String(length=80), nullable=True), schema="refs")
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT reference_id, assignment_id, assignment_rev, task_id, statement, constraints "
            "FROM refs.reference_records"
        )
    )
    for row in rows:
        connection.execute(
            sa.text("UPDATE refs.reference_records SET lookup_key = :key WHERE reference_id = :id"),
            {"key": lookupKeyV1(row), "id": row.reference_id},
        )
    op.alter_column("reference_records", "lookup_key", nullable=False, schema="refs")
    op.create_index(
        "ix_refs_reference_records_lookup_key", "reference_records", ["lookup_key"], unique=False, schema="refs"
    )


def downgrade() -> None:
    op.drop_index("ix_refs_reference_records_lookup_key", table_name="reference_records", schema="refs")
    op.drop_column("reference_records", "lookup_key", schema="refs")
