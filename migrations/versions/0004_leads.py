"""Заявки на аудит с публичного сайта

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "leads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("company", sa.String(), nullable=True),
        sa.Column("contact", sa.String(), nullable=False),
        sa.Column("niche", sa.String(), nullable=True),
        sa.Column("volume", sa.String(), nullable=False),
        sa.Column("documents", sa.String(), nullable=True),
        sa.Column("message", sa.String(), nullable=True),
        sa.Column("ip", sa.String(), nullable=True),
        sa.Column("notified", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leads")),
    )


def downgrade() -> None:
    op.drop_table("leads")
