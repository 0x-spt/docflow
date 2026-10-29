"""Правила маршрутизации тенанта: упорядоченный список «если → то», PUT заменяет его целиком.

Менять может владелец (сессия) или ключ API; оператор только читает.
"""

from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from docflow.api.deps import Principal, SessionDep, TenantDep, require_owner_or_key
from docflow.db.models import Destination, RoutingRules
from docflow.routing_model import Rule, RulesIn

router = APIRouter(prefix="/v1/routing-rules", tags=["routing"])

ManagerDep = Annotated[Principal, Depends(require_owner_or_key)]


def _dump(rules: list[Rule]) -> list[dict[str, Any]]:
    return [r.model_dump(mode="json", by_alias=True) for r in rules]


@router.get(
    "",
    summary="Правила маршрутизации",
    description=(
        "Упорядоченный список правил тенанта; без правил ничего не выгружается. "
        "`updated_at` пуст, если правил не было."
    ),
)
async def get_rules(tenant_id: TenantDep, session: SessionDep) -> dict[str, Any]:
    row = await session.get(RoutingRules, tenant_id)
    if row is None:
        return {"rules": [], "updated_at": None}
    return {"rules": row.rules, "updated_at": row.updated_at.isoformat()}


@router.put(
    "",
    summary="Сохранить правила",
    description=(
        "Заменяет весь список правил (до 100 правил, до 20 условий в каждом). Срабатывают все подходящие правила. "
        "422 — неизвестный оператор или получатель (несуществующий или чужой). "
        "403 — сессия оператора (нужен владелец или ключ API)."
    ),
)
async def put_rules(body: RulesIn, manager: ManagerDep, session: SessionDep) -> dict[str, Any]:
    tenant_id = manager.tenant_id
    wanted = {i for r in body.rules for i in (*r.export_to, *r.notify)}
    if wanted:
        own = set(
            (
                await session.execute(
                    select(Destination.id).where(Destination.tenant_id == tenant_id, Destination.id.in_(wanted))
                )
            ).scalars()
        )
        for r in body.rules:
            if any(i not in own for i in (*r.export_to, *r.notify)):
                raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"правило «{r.name}»: получатель не найден")
    now = datetime.now(UTC)
    row = await session.get(RoutingRules, tenant_id)
    if row is None:
        row = RoutingRules(tenant_id=tenant_id, rules=_dump(body.rules), updated_at=now)
        session.add(row)
    else:
        row.rules = _dump(body.rules)
        row.updated_at = now
    await session.commit()
    return {"rules": row.rules, "updated_at": row.updated_at.isoformat()}
