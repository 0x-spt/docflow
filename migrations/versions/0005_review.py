"""Ручная проверка документов: кто, когда и с какой заметкой

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch:  # batch — для SQLite в тестах
        batch.add_column(sa.Column("reviewed_by", sa.Uuid(), nullable=True))
        batch.add_column(sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True))
        batch.add_column(sa.Column("review_note", sa.String(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_documents_reviewed_by_users"), "users", ["reviewed_by"], ["id"], ondelete="SET NULL"
        )


def downgrade() -> None:
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint(op.f("fk_documents_reviewed_by_users"), type_="foreignkey")
        batch.drop_column("review_note")
        batch.drop_column("reviewed_at")
        batch.drop_column("reviewed_by")
