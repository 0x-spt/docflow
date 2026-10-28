"""Справочники тенантов и флаг auto_ok

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "dictionaries",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("entries", JSON_TYPE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_dictionaries_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_dictionaries")),
        sa.UniqueConstraint("tenant_id", "name", name=op.f("uq_dictionaries_tenant_name")),
    )
    # существующим тенантам: облачной модели доверяем, локальной — нет (как и для новых по умолчанию)
    op.add_column("tenants", sa.Column("auto_ok", sa.Boolean(), nullable=True))
    op.execute("UPDATE tenants SET auto_ok = (extractor = 'claude')")
    with op.batch_alter_table("tenants") as batch:  # batch — для SQLite в тестах
        batch.alter_column("auto_ok", nullable=False)


def downgrade() -> None:
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("auto_ok")
    op.drop_table("dictionaries")
