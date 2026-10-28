"""Коннекторы: получатели выгрузки, правила маршрутизации, журнал выгрузок

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "destinations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("config", JSON_TYPE, nullable=False),
        sa.Column("secrets_enc", sa.String(), nullable=True),
        sa.Column("mapping", JSON_TYPE, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_destinations_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_destinations")),
    )
    op.create_index("ix_destinations_tenant_id", "destinations", ["tenant_id"])
    op.create_table(
        "routing_rules",
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("rules", JSON_TYPE, nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_routing_rules_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("tenant_id", name=op.f("pk_routing_rules")),
    )
    op.create_table(
        "exports",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("destination_id", sa.Uuid(), nullable=True),
        sa.Column("purpose", sa.String(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("external_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("purpose IN ('export', 'notify_review', 'notify_new')", name=op.f("ck_exports_purpose")),
        sa.CheckConstraint("status IN ('pending', 'done', 'failed')", name=op.f("ck_exports_status")),
        sa.ForeignKeyConstraint(
            ["destination_id"],
            ["destinations.id"],
            name=op.f("fk_exports_destination_id_destinations"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], name=op.f("fk_exports_document_id_documents"), ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_exports_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_exports")),
        sa.UniqueConstraint(
            "document_id", "destination_id", "purpose", name=op.f("uq_exports_document_destination_purpose")
        ),
    )
    op.create_index("ix_exports_status_next_attempt_at", "exports", ["status", "next_attempt_at"])
    op.create_index("ix_exports_tenant_id", "exports", ["tenant_id"])


def downgrade() -> None:
    op.drop_table("exports")
    op.drop_table("routing_rules")
    op.drop_table("destinations")
