"""Заявки: куда выгружать данные (список систем)

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

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    with op.batch_alter_table("leads") as batch:  # batch — для SQLite в тестах
        # существующие заявки получают пустой список
        batch.add_column(sa.Column("targets", JSON_TYPE, nullable=False, server_default=sa.text("'[]'")))


def downgrade() -> None:
    with op.batch_alter_table("leads") as batch:
        batch.drop_column("targets")
