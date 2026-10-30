"""Свои названия полей схем у тенанта и поля очереди

Revision ID: 0010
Revises: 0009
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "field_labels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("schema_slug", sa.String(), nullable=False),
        sa.Column("labels", JSON_TYPE, nullable=False),
        sa.Column("queue_fields", JSON_TYPE, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_field_labels_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_field_labels")),
        sa.UniqueConstraint("tenant_id", "schema_slug", name=op.f("uq_field_labels_tenant_slug")),
    )


def downgrade() -> None:
    op.drop_table("field_labels")
