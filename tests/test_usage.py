import uuid
from datetime import UTC, datetime, timedelta

from docflow.db.models import Document, Tenant
from docflow.usage import limit_message, month_bounds, pages_used

NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def utc(*args) -> datetime:
    return datetime(*args, tzinfo=UTC)


def test_month_bounds_moscow():
    assert month_bounds(utc(2026, 10, 31, 21, 30)) == (utc(2026, 10, 31, 21), utc(2026, 11, 30, 21))
    assert month_bounds(utc(2026, 10, 31, 20, 59))[0] == utc(2026, 9, 30, 21)


def test_limit_message():
    assert limit_message(50, 50) == "Лимит страниц на месяц исчерпан (50 из 50)"


async def add(s, tenant_id, pages, status="ok", model=None, created_at=NOW):
    s.add(
        Document(
            id=uuid.uuid4(), tenant_id=tenant_id, schema_slug="invoice_ru", schema_version=1, status=status,
            files=[], mode="gray", pages=pages, model=model, created_at=created_at,
        )
    )


async def new_tenant(s, name="t") -> uuid.UUID:
    tenant = Tenant(name=name)
    s.add(tenant)
    await s.flush()
    return tenant.id


async def test_pages_used_counts(sm):
    async with sm() as s:
        tid = await new_tenant(s)
        await add(s, tid, 2, "queued")
        await add(s, tid, 3, "ok")
        await add(s, tid, 1, "needs_review")
        await add(s, tid, 4, "failed", model="x")
        await s.commit()
        assert await pages_used(s, tid, NOW) == 10


async def test_pages_used_skips_failed_without_model(sm):
    async with sm() as s:
        tid = await new_tenant(s)
        await add(s, tid, 5, "failed", model=None)
        await s.commit()
        assert await pages_used(s, tid, NOW) == 0


async def test_pages_used_other_tenant_and_prior_month_excluded(sm):
    async with sm() as s:
        tid = await new_tenant(s)
        other = await new_tenant(s, "other")
        await add(s, tid, 2)
        await add(s, other, 7)
        await add(s, tid, 9, created_at=NOW - timedelta(days=30))
        await add(s, tid, 4, created_at=utc(2026, 9, 30, 20, 59))  # ещё сентябрь по Москве
        await add(s, tid, 1, created_at=utc(2026, 9, 30, 21, 0))  # уже октябрь по Москве
        await s.commit()
        assert await pages_used(s, tid, NOW) == 3
