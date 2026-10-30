"""Время последнего обновления от Telegram: по нему опрос забывает offset, пока нумерация не сменилась

Revision ID: 0012
Revises: 0011
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("tenant_bots") as batch:
        batch.add_column(sa.Column("last_update_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("tenant_bots") as batch:
        batch.drop_column("last_update_at")
