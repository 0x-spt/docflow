"""Журнал выгрузок: статус skipped — доставка больше не нужна (уведомление устарело), это не ошибка

Revision ID: 0009
Revises: 0008
"""

from alembic import op

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None

NEW = "status IN ('pending', 'done', 'failed', 'skipped')"
OLD = "status IN ('pending', 'done', 'failed')"


def upgrade() -> None:
    with op.batch_alter_table("exports") as batch:  # batch — для SQLite в тестах
        batch.drop_constraint(op.f("ck_exports_status"), type_="check")
        batch.create_check_constraint(op.f("ck_exports_status"), NEW)


def downgrade() -> None:
    # пропущенные в старой схеме — неудачные (уведомление не ушло)
    op.execute("UPDATE exports SET status = 'failed' WHERE status = 'skipped'")
    with op.batch_alter_table("exports") as batch:
        batch.drop_constraint(op.f("ck_exports_status"), type_="check")
        batch.create_check_constraint(op.f("ck_exports_status"), OLD)
