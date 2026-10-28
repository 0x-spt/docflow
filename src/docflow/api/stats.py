"""Счётчики документов тенанта для кабинета."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from docflow.api.deps import SessionDep, TenantDep
from docflow.db.models import Document

router = APIRouter(prefix="/v1/stats", tags=["stats"])

STATUSES = ("queued", "processing", "ok", "needs_review", "failed")


@router.get(
    "",
    summary="Статистика за период",
    description=(
        "Документы, созданные за последние `days` дней (1–365, по умолчанию 30): всего и по статусам. "
        "`auto_share` — доля `ok` среди завершённых (ok + needs_review + failed), null, если завершённых нет. "
        "`reviewed` — сколько документов проверено человеком."
    ),
)
async def get_stats(
    tenant_id: TenantDep, session: SessionDep, days: Annotated[int, Query(ge=1, le=365)] = 30
) -> dict:
    since = datetime.now(UTC) - timedelta(days=days)
    rows = await session.execute(
        select(Document.status, func.count())
        .where(Document.tenant_id == tenant_id, Document.created_at >= since)
        .group_by(Document.status)
    )
    by_status = dict.fromkeys(STATUSES, 0)
    by_status.update(dict(rows.all()))
    finished = by_status["ok"] + by_status["needs_review"] + by_status["failed"]
    return {
        "days": days,
        "total": sum(by_status.values()),
        "by_status": by_status,
        "auto_share": by_status["ok"] / finished if finished else None,
        "reviewed": 0,  # колонка reviewed_at появится вместе с проверкой (Task 4)
    }
