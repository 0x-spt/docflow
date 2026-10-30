"""Учёт страниц за календарный месяц по Москве. Без импорта intake: лимит проверяет intake.check_limit."""

import uuid
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.models import Document

BILLING_TZ = ZoneInfo("Europe/Moscow")


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    """[начало, начало следующего) календарного месяца по Москве, в UTC."""
    local = now.astimezone(BILLING_TZ)
    start = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = start.replace(year=start.year + 1, month=1) if start.month == 12 else start.replace(month=start.month + 1)
    return start.astimezone(UTC), end.astimezone(UTC)


async def pages_used(session: AsyncSession, tenant_id: uuid.UUID, now: datetime | None = None) -> int:
    """Страницы документов месяца. Упавшие до обращения к модели (failed без model) не считаются."""
    start, end = month_bounds(now or datetime.now(UTC))
    total = await session.scalar(
        select(func.coalesce(func.sum(Document.pages), 0)).where(
            Document.tenant_id == tenant_id,
            Document.created_at >= start,
            Document.created_at < end,
            ~and_(Document.status == "failed", Document.model.is_(None)),
        )
    )
    return int(total)


def limit_message(used: int, limit: int) -> str:
    return f"Лимит страниц на месяц исчерпан ({used} из {limit})"
