from types import SimpleNamespace

import pytest

from docflow.plans import DEFAULT_PLAN, PLANS, apply_plan


def test_plans_table():
    assert {c: (p.title, p.pages_limit) for c, p in PLANS.items()} == {
        "trial": ("Пробный", 50),
        "start": ("Старт", 500),
        "business": ("Бизнес", 3000),
        "unlimited": ("Без лимита", None),
    }
    assert DEFAULT_PLAN == "trial"


def test_apply_plan():
    tenant = SimpleNamespace(plan="trial", pages_limit=50)
    apply_plan(tenant, "business")
    assert (tenant.plan, tenant.pages_limit) == ("business", 3000)
    apply_plan(tenant, "unlimited")
    assert (tenant.plan, tenant.pages_limit) == ("unlimited", None)


def test_apply_unknown_plan_keyerror():
    with pytest.raises(KeyError):
        apply_plan(SimpleNamespace(plan="trial", pages_limit=50), "gold")
