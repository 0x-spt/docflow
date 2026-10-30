"""Каталог тарифов: код, название, лимит страниц в месяц (None — без лимита). Без импорта моделей."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Plan:
    code: str
    title: str
    pages_limit: int | None


PLANS: dict[str, Plan] = {
    p.code: p
    for p in (
        Plan("trial", "Пробный", 50),
        Plan("start", "Старт", 500),
        Plan("business", "Бизнес", 3000),
        Plan("unlimited", "Без лимита", None),
    )
}
DEFAULT_PLAN = "trial"


def apply_plan(tenant, code: str) -> None:
    """Записать тариф и его лимит в тенанта. Неизвестный код — KeyError."""
    plan = PLANS[code]
    tenant.plan = plan.code
    tenant.pages_limit = plan.pages_limit
