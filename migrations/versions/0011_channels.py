"""Каналы приёма: бот и ящик тенанта, тариф, источник документа, выгрузки в чаты бота

Revision ID: 0011
Revises: 0010
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")

PURPOSE_NEW = "purpose IN ('export', 'notify_review', 'notify_new', 'bot_ready', 'bot_reviewed')"
PURPOSE_OLD = "purpose IN ('export', 'notify_review', 'notify_new')"
SOURCE = "source IN ('api', 'cabinet', 'telegram', 'email')"


def upgrade() -> None:
    # существующие тенанты остаются без лимита; пробный тариф получают только новые (из ORM)
    with op.batch_alter_table("tenants") as batch:
        batch.add_column(sa.Column("plan", sa.String(), nullable=False, server_default="unlimited"))
        batch.add_column(sa.Column("pages_limit", sa.Integer(), nullable=True))
    with op.batch_alter_table("tenants") as batch:
        batch.alter_column("plan", existing_type=sa.String(), existing_nullable=False, server_default=None)

    with op.batch_alter_table("documents") as batch:
        batch.add_column(sa.Column("source", sa.String(), nullable=False, server_default="api"))
        batch.add_column(sa.Column("source_ref", JSON_TYPE, nullable=True))
        batch.create_check_constraint(op.f("ck_documents_source"), SOURCE)

    op.create_table(
        "tenant_bots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("token_enc", sa.String(), nullable=False),
        sa.Column("token_hash", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("invite_code", sa.String(), nullable=False),
        sa.Column("default_schema", sa.String(), nullable=True),
        sa.Column("update_offset", sa.BigInteger(), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_tenant_bots_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tenant_bots")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_tenant_bots_tenant")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_tenant_bots_token_hash")),
    )
    op.create_table(
        "bot_chats",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("bot_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("all_documents", sa.Boolean(), nullable=False),
        sa.Column("linked_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["bot_id"], ["tenant_bots.id"], name=op.f("fk_bot_chats_bot_id_tenant_bots"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_bot_chats")),
        sa.UniqueConstraint("bot_id", "chat_id", name=op.f("uq_bot_chats_bot_chat")),
    )
    op.create_table(
        "mailboxes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.Uuid(), nullable=False),
        sa.Column("host", sa.String(), nullable=False),
        sa.Column("port", sa.Integer(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_enc", sa.String(), nullable=False),
        sa.Column("folder", sa.String(), nullable=False),
        sa.Column("allowed_senders", JSON_TYPE, nullable=False),
        sa.Column("default_schema", sa.String(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("last_error", sa.String(), nullable=True),
        sa.Column("last_checked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["tenant_id"], ["tenants.id"], name=op.f("fk_mailboxes_tenant_id_tenants"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mailboxes")),
        sa.UniqueConstraint("tenant_id", name=op.f("uq_mailboxes_tenant")),
    )

    with op.batch_alter_table("exports") as batch:
        batch.add_column(sa.Column("bot_chat_id", sa.Uuid(), nullable=True))
        batch.create_foreign_key(
            op.f("fk_exports_bot_chat_id_bot_chats"), "bot_chats", ["bot_chat_id"], ["id"], ondelete="SET NULL"
        )
        batch.drop_constraint(op.f("ck_exports_purpose"), type_="check")
        batch.create_check_constraint(op.f("ck_exports_purpose"), PURPOSE_NEW)
    op.create_index(
        "uq_exports_document_chat_purpose",
        "exports",
        ["document_id", "bot_chat_id", "purpose"],
        unique=True,
        postgresql_where=sa.text("bot_chat_id IS NOT NULL"),
        sqlite_where=sa.text("bot_chat_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.execute("DELETE FROM exports WHERE purpose IN ('bot_ready', 'bot_reviewed')")
    op.drop_index("uq_exports_document_chat_purpose", table_name="exports")
    with op.batch_alter_table("exports") as batch:
        batch.drop_constraint(op.f("ck_exports_purpose"), type_="check")
        batch.create_check_constraint(op.f("ck_exports_purpose"), PURPOSE_OLD)
        batch.drop_constraint(op.f("fk_exports_bot_chat_id_bot_chats"), type_="foreignkey")
        batch.drop_column("bot_chat_id")
    op.drop_table("mailboxes")
    op.drop_table("bot_chats")
    op.drop_table("tenant_bots")
    with op.batch_alter_table("documents") as batch:
        batch.drop_constraint(op.f("ck_documents_source"), type_="check")
        batch.drop_column("source_ref")
        batch.drop_column("source")
    with op.batch_alter_table("tenants") as batch:
        batch.drop_column("pages_limit")
        batch.drop_column("plan")
