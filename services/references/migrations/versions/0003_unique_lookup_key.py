"""make lookup key unique

Revision ID: 0003
Revises: 0002

One lookup key means one referenceId, also for workers in different processes.
The unique index is what INSERT ... ON CONFLICT (lookup_key) DO NOTHING relies on.
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

INDEX_NAME = "ix_refs_reference_records_lookup_key"


def upgrade() -> None:
    duplicates = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT lookup_key, string_agg(reference_id, ', ' ORDER BY created_at, reference_id) AS ids"
                " FROM refs.reference_records GROUP BY lookup_key HAVING count(*) > 1 LIMIT 5"
            )
        )
        .all()
    )
    if duplicates:
        listed = "; ".join(f"{row.lookup_key}: {row.ids}" for row in duplicates)
        raise RuntimeError(
            "refs.reference_records has several rows per lookup key, so it cannot be made unique. "
            f"Decide which referenceId to keep before migrating. {listed}"
        )
    op.drop_index(INDEX_NAME, table_name="reference_records", schema="refs")
    op.create_index(INDEX_NAME, "reference_records", ["lookup_key"], unique=True, schema="refs")


def downgrade() -> None:
    op.drop_index(INDEX_NAME, table_name="reference_records", schema="refs")
    op.create_index(INDEX_NAME, "reference_records", ["lookup_key"], unique=False, schema="refs")
