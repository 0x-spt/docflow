"""Начальная схема

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def _id():
    return sa.Column("id", sa.Uuid(), nullable=False)


def _created():
    return sa.Column("created_at", sa.DateTime(timezone=True), nullable=False)


def upgrade() -> None:
    op.create_table(
        "tenants",
        _id(),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("extractor", sa.String(), nullable=False),
        _created(),
        sa.CheckConstraint("extractor IN ('claude', 'ollama')", name=op.f("ck_tenants_extractor")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenants")),
    )
    op.create_table(
        "api_keys",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("prefix", sa.String(), nullable=False),
        sa.Column("key_hash", sa.String(), nullable=False),
        _created(),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_api_keys_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_keys")),
    )
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)
    op.create_table(
        "schemas",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=True),
        sa.Column("slug", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("body", JSON_TYPE, nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_schemas_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_schemas")),
        sa.UniqueConstraint("tenant_id", "slug", "version", name=op.f("uq_schemas_tenant_slug_version")),
    )
    op.create_index(
        "uq_schemas_builtin_slug_version",
        "schemas",
        ["slug", "version"],
        unique=True,
        postgresql_where=sa.text("tenant_id IS NULL"),
        sqlite_where=sa.text("tenant_id IS NULL"),
    )
    op.create_table(
        "documents",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("schema_slug", sa.String(), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("idempotency_key", sa.String(), nullable=True),
        sa.Column("files", JSON_TYPE, nullable=False),
        sa.Column("mode", sa.String(), nullable=False),
        sa.Column("data", JSON_TYPE, nullable=True),
        sa.Column("issues", JSON_TYPE, nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("pages", sa.Integer(), nullable=False),
        _created(),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'ok', 'needs_review', 'failed')", name=op.f("ck_documents_status")
        ),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_documents_tenant_id_tenants"), ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name=op.f("uq_documents_tenant_idempotency_key")),
    )
    op.create_index("ix_documents_tenant_id_created_at", "documents", ["tenant_id", "created_at"])
    op.create_table(
        "webhook_endpoints",
        _id(),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("url", sa.String(), nullable=False),
        sa.Column("secret", sa.String(), nullable=False),
        sa.Column("events", JSON_TYPE, nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        _created(),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_webhook_endpoints_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_endpoints")),
    )
    op.create_table(
        "webhook_deliveries",
        _id(),
        sa.Column("endpoint_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("event", sa.String(), nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("response_code", sa.Integer(), nullable=True),
        _created(),
        sa.CheckConstraint("status IN ('pending', 'delivered', 'failed')", name=op.f("ck_webhook_deliveries_status")),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name=op.f("fk_webhook_deliveries_document_id_documents"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["endpoint_id"],
            ["webhook_endpoints.id"],
            name=op.f("fk_webhook_deliveries_endpoint_id_webhook_endpoints"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_webhook_deliveries")),
        sa.UniqueConstraint(
            "endpoint_id", "document_id", "event", name=op.f("uq_webhook_deliveries_endpoint_document_event")
        ),
    )
    op.create_index(
        "ix_webhook_deliveries_status_next_attempt_at", "webhook_deliveries", ["status", "next_attempt_at"]
    )


def downgrade() -> None:
    op.drop_table("webhook_deliveries")
    op.drop_table("webhook_endpoints")
    op.drop_table("documents")
    op.drop_table("schemas")
    op.drop_table("api_keys")
    op.drop_table("tenants")
