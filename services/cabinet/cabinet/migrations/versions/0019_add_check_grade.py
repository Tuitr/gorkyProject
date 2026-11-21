"""Store the teacher's grade for a checked submission: an integer from 1 to 5, or none yet."""

import sqlalchemy as sa
from alembic import op

revision = "0019"
down_revision = "0018"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("attempt_checks", sa.Column("grade", sa.SmallInteger(), nullable=True), schema="cabinet")
    op.create_check_constraint(
        "ck_attempt_checks_grade", "attempt_checks", "grade IS NULL OR grade BETWEEN 1 AND 5", schema="cabinet",
    )


def downgrade() -> None:
    op.drop_constraint("ck_attempt_checks_grade", "attempt_checks", schema="cabinet", type_="check")
    op.drop_column("attempt_checks", "grade", schema="cabinet")
