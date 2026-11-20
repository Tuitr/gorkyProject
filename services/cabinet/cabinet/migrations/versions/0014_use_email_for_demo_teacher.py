"""Preserve the original synthetic teacher when public sessions start requiring email."""

from alembic import op

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        UPDATE cabinet.teachers
        SET login = 'teacher.demo@example.org'
        WHERE login = 'teacher.demo' AND display_name = 'Демо Учитель'
          AND NOT EXISTS (SELECT 1 FROM cabinet.teachers WHERE login = 'teacher.demo@example.org')
    """)


def downgrade() -> None:
    # The previous sessions API accepted email addresses as login values.
    pass
