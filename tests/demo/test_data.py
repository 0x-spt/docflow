import uuid
from collections import Counter
from datetime import date

import pytest

from docflow.demo.data import _ITEMS as ITEMS
from docflow.demo.data import (
    ACT_SCHEMA,
    BUYER,
    DEMO_TENANT_NAME,
    RULES_TEMPLATE,
    SUPPLIERS_DICT,
    DemoDestinations,
    DemoExtractor,
    scenarios,
)
from docflow.extraction.base import ExtractionError
from docflow.extraction.normalize import normalize
from docflow.pipeline import APPROVAL_REQUIRED, approval_issues
from docflow.routing import evaluate_rules
from docflow.routing_model import RulesIn
from docflow.schemas.library import builtin_schemas
from docflow.schemas.model import DocSchema
from docflow.validation.checksums import valid_account, valid_bik, valid_inn, valid_kpp
from docflow.validation.dictionaries import DictEntry, resolve_dictionaries
from docflow.validation.engine import decide_status, evaluate

TODAY = date(2026, 10, 29)
DEST = DemoDestinations(
    mail_accounting=uuid.UUID(int=1),
    mail_operator=uuid.UUID(int=2),
    sheets=uuid.UUID(int=3),
    bitrix=uuid.UUID(int=4),
    telegram=uuid.UUID(int=5),
)
ENTRIES = {"suppliers": [DictEntry(e["value"], tuple(e["aliases"])) for e in SUPPLIERS_DICT["entries"]]}


def _schemas() -> dict[str, DocSchema]:
    return {**builtin_schemas(), "act_works": DocSchema.model_validate(ACT_SCHEMA)}


def _issues(schema: DocSchema, answer: dict, today: date):
    """Тот же путь, что pipeline._run (auto_ok=True)."""
    data, issues = normalize(schema, answer)
    issues += resolve_dictionaries(schema, data, ENTRIES)
    issues += evaluate(schema, data, today=today)
    rules = RulesIn.model_validate({"rules": RULES_TEMPLATE(DEST)}).rules
    decision = evaluate_rules(rules, schema, data, ENTRIES)
    if decide_status(issues) == "ok":
        issues += approval_issues(decision)
    return issues


def test_counts():
    sc = scenarios(TODAY)
    assert len(sc) == 24
    assert len({s.key for s in sc}) == 24
    assert all(s.key.isascii() for s in sc)
    assert Counter(s.expect for s in sc) == {"ok": 17, "needs_review": 6, "failed": 1}
    assert Counter(s.schema for s in sc) == {"invoice_ru": 10, "upd_ru": 8, "act_works": 6}
    assert Counter(s.review for s in sc if s.review) == {"approve": 1, "approve_edit": 1, "reject": 1}
    failed = [s for s in sc if s.expect == "failed"]
    assert [(s.key, s.answer) for s in failed] == [("blurry_photo", None)]
    rejected = next(s for s in sc if s.review == "reject")
    assert rejected.review_note.startswith("Дубль счёта № ")
    assert all(s.expect == "needs_review" for s in sc if s.review)
    edit = next(s for s in sc if s.review == "approve_edit")
    assert edit.fix
    assert all(s.fix is None for s in sc if s.review != "approve_edit")


@pytest.mark.parametrize("sc", [s for s in scenarios(TODAY) if s.expect != "failed"], ids=lambda s: s.key)
def test_expected_status(sc):
    issues = _issues(_schemas()[sc.schema], sc.answer, TODAY)
    assert decide_status(issues) == sc.expect, issues


def test_needs_review_reasons():
    """Причины: две правки сумм, три согласования (одно — дубль), справочник."""
    reasons = {}
    for sc in scenarios(TODAY):
        if sc.expect == "needs_review":
            codes = {i.code for i in _issues(_schemas()[sc.schema], sc.answer, TODAY) if i.code != "corrected"}
            reasons[sc.key] = codes
    assert sorted(len(c) > 0 for c in reasons.values()) == [True] * 6
    flat = Counter(code for c in reasons.values() for code in c)
    assert flat[APPROVAL_REQUIRED] == 3
    assert flat["not_in_dictionary"] == 1
    assert flat["sum_mismatch"] >= 2


def test_approve_edit_fix_is_ok():
    sc = next(s for s in scenarios(TODAY) if s.review == "approve_edit")
    issues = _issues(_schemas()[sc.schema], {**sc.answer, **sc.fix}, TODAY)
    assert decide_status([i for i in issues if i.code != APPROVAL_REQUIRED]) == "ok", issues


def test_reject_is_duplicate_of_earlier_invoice():
    sc = scenarios(TODAY)
    dup = next(s for s in sc if s.review == "reject")
    orig = [s for s in sc if s is not dup and s.answer and s.answer.get("number") == dup.answer["number"]]
    assert len(orig) == 1 and orig[0].schema == dup.schema
    assert orig[0].days_ago > dup.days_ago
    assert dup.review_note == f"Дубль счёта № {dup.answer['number']}"


# 2026-10-26 — понедельник, 2026-11-01 и 2026-03-01 — воскресенья, 2026-10-31 — суббота
DAYS = [date(2026, 3, 1), date(2026, 12, 31), date(2026, 11, 2), date(2026, 10, 26), date(2026, 11, 1),
        date(2026, 10, 31), TODAY]


@pytest.mark.parametrize("today", DAYS, ids=str)
def test_scenarios_dates_in_window(today):
    for sc in scenarios(today):
        assert 1 <= sc.days_ago <= 20, sc.key
        assert 9 <= sc.hour <= 17, sc.key
        created = date.fromordinal(today.toordinal() - sc.days_ago)
        assert created.weekday() < 5, (sc.key, created)
        if sc.answer is None:
            continue
        issued = date.fromisoformat(sc.answer["issued"])
        assert issued <= created and issued.weekday() < 5, sc.key
        assert 0 <= (created - issued).days <= 5, sc.key


@pytest.mark.parametrize("today", DAYS, ids=str)
def test_reviewed_far_enough(today):
    """Проверка, перенесённая на следующий будний день, должна остаться в прошлом."""
    for sc in scenarios(today):
        if sc.review:
            assert sc.days_ago >= 4, sc.key


def test_geography():
    """Покупатель в Екатеринбурге; издалека — не больше двух поставщиков и только товары."""
    from docflow.demo.data import _OUTSIDER, _SUPPLIERS, BETON, KARPENKO, SAVELIEVA

    assert BUYER.inn[:2] == "66" and "Екатеринбург" in BUYER.address
    companies = [s.company for s in _SUPPLIERS] + [_OUTSIDER]
    far = [c for c in companies if c.inn[:2] != "66"]
    assert len(far) <= 2
    for c in companies:
        assert ("Свердловская" in c.address or "Екатеринбург" in c.address) == (c.inn[:2] == "66"), c.name
    for local in (BETON, KARPENKO, SAVELIEVA):
        assert local.company.inn[:2] == "66"
    # всё, что делается на объекте, — местные подрядчики
    on_site = {"concrete", "pump", "unloading", "excavator", "roof_work", "steel_work", "electro_work", "cleanup",
               "waste", "base_work", "delivery"}
    names = {ITEMS[k][0] for k in on_site}
    for sc in scenarios(TODAY):
        if sc.answer and any(r["name"] in names for r in sc.answer["items"]):
            inn = sc.answer.get("supplier_inn") or sc.answer.get("contractor_inn")
            assert inn[:2] == "66", sc.key


def test_requisites_valid():
    for sc in scenarios(TODAY):
        a = sc.answer
        if a is None:
            continue
        if sc.schema == "act_works":
            assert valid_inn(a["contractor_inn"]), sc.key
            assert a["customer"] == BUYER.name
            continue
        assert valid_inn(a["supplier_inn"]), sc.key
        assert a["supplier_kpp"] is None if len(a["supplier_inn"]) == 12 else valid_kpp(a["supplier_kpp"]), sc.key
        assert a["buyer"] == BUYER.name and a["buyer_inn"] == BUYER.inn
        assert valid_bik(a["bik"]), sc.key
        if sc.schema == "invoice_ru":
            assert valid_account(a["account"], a["bik"]), sc.key
        else:
            assert a["buyer_kpp"] == BUYER.kpp
    assert valid_inn(BUYER.inn) and valid_kpp(BUYER.kpp)
    answers = [sc.answer for sc in scenarios(TODAY) if sc.answer]
    all_inns = {a.get("supplier_inn") or a.get("contractor_inn") for a in answers}
    assert "7707083893" not in all_inns and BUYER.inn not in all_inns
    assert all(i[2:4] == "00" for i in all_inns | {BUYER.inn})


def test_suppliers_dict():
    assert SUPPLIERS_DICT["title"] == "Поставщики"
    assert len(SUPPLIERS_DICT["entries"]) == 8
    assert len({e["value"] for e in SUPPLIERS_DICT["entries"]}) == 8


def test_rules_valid():
    bare = DemoDestinations(mail_accounting=uuid.UUID(int=1), mail_operator=uuid.UUID(int=2))
    for dest in (bare, DEST):
        rules = RulesIn.model_validate({"rules": RULES_TEMPLATE(dest)}).rules
        assert all(None not in r.export_to and None not in r.notify for r in rules)
    rules = RulesIn.model_validate({"rules": RULES_TEMPLATE(bare)}).rules
    assert all(r.export_to == [] for r in rules)
    assert [r.notify for r in rules if r.schema_slug is None] == [[bare.mail_operator]]
    full = RulesIn.model_validate({"rules": RULES_TEMPLATE(DEST)}).rules
    assert sum(r.require_approval for r in full) == 2
    assert {r.schema_slug for r in full if r.require_approval} == {"invoice_ru", "upd_ru"}


def test_act_schema_valid():
    s = DocSchema.model_validate(ACT_SCHEMA)
    assert (s.slug, s.version, s.title) == ("act_works", 1, "Акт выполненных работ")
    names = {f.name for f in s.fields}
    assert names == {"number", "issued", "contractor", "contractor_inn", "customer", "items", "total"}


def test_deterministic():
    assert scenarios(TODAY) == scenarios(TODAY)


def test_demo_extractor():
    answer = {"number": "1"}
    ext = DemoExtractor(answer)
    assert ext.model == "demo"
    res = ext.extract(_schemas()["invoice_ru"], [])
    assert res.raw == answer and res.raw is not answer and res.model == "demo"
    with pytest.raises(ExtractionError, match="размыто"):
        DemoExtractor(None).extract(_schemas()["invoice_ru"], [])


def test_tenant_name():
    assert DEMO_TENANT_NAME == "Демо — ООО «Северный Путь»"
