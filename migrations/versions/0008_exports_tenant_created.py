"""Журнал выгрузок: индекс (tenant_id, created_at) вместо (tenant_id)

Revision ID: 0008
Revises: 0007
"""

from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_exports_tenant_id_created_at", "exports", ["tenant_id", "created_at"])
    # составной индекс покрывает и выборки по одному tenant_id
    op.drop_index("ix_exports_tenant_id", table_name="exports")


def downgrade() -> None:
    op.create_index("ix_exports_tenant_id", "exports", ["tenant_id"])
    op.drop_index("ix_exports_tenant_id_created_at", table_name="exports")
