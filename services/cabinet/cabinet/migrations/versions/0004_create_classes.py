"""Store teacher-owned classes and their students."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "classes",
        sa.Column("class_id", sa.String(36), primary_key=True),
        sa.Column("teacher_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.ForeignKeyConstraint(["teacher_id"], ["cabinet.teachers.teacher_id"], ondelete="CASCADE"),
        schema="cabinet",
    )
    op.create_index("ix_cabinet_classes_teacher_id", "classes", ["teacher_id"], schema="cabinet")
    op.create_table(
        "students",
        sa.Column("student_id", sa.String(36), primary_key=True),
        sa.Column("class_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.ForeignKeyConstraint(["class_id"], ["cabinet.classes.class_id"], ondelete="CASCADE"),
        schema="cabinet",
    )
    op.create_index("ix_cabinet_students_class_id", "students", ["class_id"], schema="cabinet")


def downgrade() -> None:
    op.drop_table("students", schema="cabinet")
    op.drop_table("classes", schema="cabinet")
