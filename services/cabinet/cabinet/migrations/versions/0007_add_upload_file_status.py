"""Track the initial binding status of uploaded documents."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "upload_files",
        sa.Column("status", sa.String(32), server_default="pending_binding", nullable=False),
        schema="cabinet",
    )


def downgrade() -> None:
    op.drop_column("upload_files", "status", schema="cabinet")
