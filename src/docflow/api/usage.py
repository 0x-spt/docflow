"""Тариф и расход страниц за текущий месяц."""

from datetime import UTC, datetime

from fastapi import APIRouter

from docflow.api.deps import SessionDep, TenantDep
from docflow.db.models import Tenant
from docflow.plans import PLANS
from docflow.usage import month_bounds, pages_used

router = APIRouter(prefix="/v1/usage", tags=["usage"])


@router.get(
    "",
    summary="Тариф и расход страниц",
    description=(
        "Тариф клиента и сколько страниц использовано в текущем календарном месяце (по Москве). "
        "`pages_limit` — null для тарифа без лимита."
    ),
)
async def get_usage(tenant_id: TenantDep, session: SessionDep) -> dict:
    tenant = await session.get(Tenant, tenant_id)
    now = datetime.now(UTC)
    start, end = month_bounds(now)
    return {
        "plan": tenant.plan,
        "plan_title": PLANS[tenant.plan].title,
        "pages_limit": tenant.pages_limit,
        "pages_used": await pages_used(session, tenant_id, now),
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
    }
