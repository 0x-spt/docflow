from datetime import date

import pytest

from docflow.schemas.model import DOC_RULES, FIELD_RULES, DocSchema
from docflow.validation.engine import (
    DOC_REGISTRY,
    FIELD_REGISTRY,
    KNOWN_RULES,
    Issue,
    decide_status,
    evaluate,
)

BIK = "044525225"
ACCOUNT = "40702810200000000001"  # валидный р/с для этого БИК (ключ подобран по весам 7,1,3)
TODAY = date(2026, 10, 27)


def schema(fields, rules=()):
    return DocSchema.model_validate(
        {"slug": "t_doc", "title": "T", "description": "d", "fields": fields, "rules": list(rules)}
    )


def codes(issues):
    return [(i.field, i.code) for i in issues]


ITEMS = {
    "name": "items",
    "type": "table",
    "columns": [
        {"name": "qty", "type": "number"},
        {"name": "price", "type": "number"},
        {"name": "amount", "type": "number"},
    ],
}
SUM_RULE = {"kind": "table_sum_equals", "params": {"table": "items", "column": "amount", "total": "total"}}
PRODUCT_RULE = {
    "kind": "table_row_product",
    "params": {"table": "items", "qty": "qty", "price": "price", "amount": "amount"},
}
INVOICE = schema([ITEMS, {"name": "total", "type": "number"}], [SUM_RULE, PRODUCT_RULE])


def rows(*r):
    return [{"qty": q, "price": p, "amount": a} for q, p, a in r]


def test_registries_match_schema_rules():
    assert set(FIELD_REGISTRY) == FIELD_RULES
    assert set(DOC_REGISTRY) == DOC_RULES
    assert KNOWN_RULES == FIELD_RULES | DOC_RULES


def test_unknown_rule_rejected():
    with pytest.raises(ValueError):
        schema([{"name": "a", "type": "string", "rules": [{"kind": "nope"}]}])
    with pytest.raises(ValueError):
        schema([{"name": "a", "type": "string"}], [{"kind": "nope"}])


def test_missing_required():
    s = schema([{"name": "number", "type": "string", "required": True}])
    issues = evaluate(s, {"number": None}, today=TODAY)
    assert codes(issues) == [("number", "missing_required")]
    assert "number" in issues[0].message
    assert evaluate(s, {}, today=TODAY)[0].code == "missing_required"
    assert evaluate(s, {"number": "  "}, today=TODAY)[0].code == "missing_required"
    assert evaluate(s, {"number": "42"}, today=TODAY) == []


def test_zero_and_false_are_not_missing():
    s = schema([{"name": "n", "type": "number", "required": True}, {"name": "b", "type": "boolean", "required": True}])
    assert evaluate(s, {"n": 0, "b": False}, today=TODAY) == []


def test_missing_required_empty_table():
    s = schema([{**ITEMS, "required": True}])
    assert codes(evaluate(s, {"items": []}, today=TODAY)) == [("items", "missing_required")]


def test_uncertain_fields_become_issues():
    s = schema([{"name": "total", "type": "number"}])
    issues = evaluate(s, {"total": 5, "_uncertain": ["total"]}, today=TODAY)
    assert codes(issues) == [("total", "uncertain")]


def test_sum_mismatch():
    data = {"items": rows((2, 100, 200), (1, 50, 50)), "total": 260}
    issues = evaluate(INVOICE, data, today=TODAY)
    assert codes(issues) == [("total", "sum_mismatch")]
    assert "260" in issues[0].message and "250" in issues[0].message
    assert evaluate(INVOICE, {**data, "total": 250}, today=TODAY) == []
    assert evaluate(INVOICE, {**data, "total": 250.004}, today=TODAY) == []


def test_sum_skipped_when_total_or_table_missing():
    assert evaluate(INVOICE, {"items": rows((1, 1, 1)), "total": None}, today=TODAY) == []
    assert evaluate(INVOICE, {"items": None, "total": 5}, today=TODAY) == []


def test_sum_with_null_cell_still_compares():
    data = {"items": rows((1, 10, 10), (1, 5, None)), "total": 15}
    # пустая ячейка не попадает в сумму -> 10 != 15, человек посмотрит
    assert codes(evaluate(INVOICE, data, today=TODAY)) == [("total", "sum_mismatch")]


def test_row_mismatch():
    data = {"items": rows((2, 100, 200), (3, 50, 140)), "total": 340}
    issues = evaluate(INVOICE, data, today=TODAY)
    assert codes(issues) == [("items[1].amount", "row_mismatch")]
    assert "150" in issues[0].message and "140" in issues[0].message


def test_row_skipped_with_null_cell():
    data = {"items": rows((2, None, 200)), "total": 200}
    assert evaluate(INVOICE, data, today=TODAY) == []


def test_rule_skipped_when_value_null():
    s = schema([{"name": "inn", "type": "string", "rules": [{"kind": "inn"}]}])
    assert evaluate(s, {"inn": None}, today=TODAY) == []
    assert evaluate(s, {}, today=TODAY) == []


def test_checksum_issue():
    s = schema([{"name": "inn", "type": "string", "rules": [{"kind": "inn"}]}])
    assert evaluate(s, {"inn": "7707083893"}, today=TODAY) == []
    issues = evaluate(s, {"inn": "7707083894"}, today=TODAY)
    assert codes(issues) == [("inn", "checksum")]
    assert "7707083894" in issues[0].message


@pytest.mark.parametrize(
    "kind,good,bad",
    [
        ("kpp", "770701001", "7707"),
        ("ogrn", "1027700132195", "1027700132196"),
        ("snils", "112-233-445 95", "112-233-445 96"),
        ("bik", "044525225", "014525225"),
    ],
)
def test_other_checksum_rules(kind, good, bad):
    s = schema([{"name": "f", "type": "string", "rules": [{"kind": kind}]}])
    assert evaluate(s, {"f": good}, today=TODAY) == []
    assert codes(evaluate(s, {"f": bad}, today=TODAY)) == [("f", "checksum")]


def test_regex_rule():
    s = schema([{"name": "series", "type": "string", "rules": [{"kind": "regex", "params": {"pattern": r"^\d{4}$"}}]}])
    assert evaluate(s, {"series": "4515"}, today=TODAY) == []
    assert codes(evaluate(s, {"series": "45 15"}, today=TODAY)) == [("series", "pattern")]


def test_date_not_future():
    s = schema([{"name": "d", "type": "date", "rules": [{"kind": "date_not_future"}]}])
    assert evaluate(s, {"d": "2026-10-27"}, today=TODAY) == []
    assert codes(evaluate(s, {"d": "2026-10-28"}, today=TODAY)) == [("d", "date_future")]
    assert codes(evaluate(s, {"d": "2999-01-01"})) == [("d", "date_future")]  # реальный today


def test_unparseable_date_is_flagged_not_trusted():
    s = schema([{"name": "d", "type": "date", "rules": [{"kind": "date_not_future"}]}])
    assert codes(evaluate(s, {"d": "вчера"}, today=TODAY)) == [("d", "pattern")]


def test_date_order():
    s = schema(
        [{"name": "a", "type": "date"}, {"name": "b", "type": "date"}],
        [{"kind": "date_order", "params": {"earlier": "a", "later": "b"}}],
    )
    assert evaluate(s, {"a": "1990-03-05", "b": "2010-01-01"}, today=TODAY) == []
    assert evaluate(s, {"a": "2010-01-01", "b": "2010-01-01"}, today=TODAY) == []
    assert codes(evaluate(s, {"a": "2011-01-01", "b": "2010-01-01"}, today=TODAY)) == [("b", "date_order")]
    assert evaluate(s, {"a": None, "b": "2010-01-01"}, today=TODAY) == []


def test_account_matches_bik():
    s = schema(
        [{"name": "account", "type": "string"}, {"name": "bik", "type": "string"}],
        [{"kind": "account_matches_bik", "params": {"account": "account", "bik": "bik"}}],
    )
    assert evaluate(s, {"account": ACCOUNT, "bik": BIK}, today=TODAY) == []
    bad = ACCOUNT[:-1] + str((int(ACCOUNT[-1]) + 1) % 10)
    assert codes(evaluate(s, {"account": bad, "bik": BIK}, today=TODAY)) == [("account", "checksum")]
    assert evaluate(s, {"account": ACCOUNT, "bik": None}, today=TODAY) == []
    assert evaluate(s, {"account": None, "bik": BIK}, today=TODAY) == []


ACC_RULE = {"kind": "account_matches_bik", "params": {"account": "account", "bik": "bik"}}


def test_invalid_bik_reported_without_field_level_bik_rule():
    s = schema([{"name": "account", "type": "string"}, {"name": "bik", "type": "string"}], [ACC_RULE])
    issues = evaluate(s, {"account": "40702810200000000002", "bik": "014525225"}, today=TODAY)
    assert codes(issues) == [("bik", "checksum")]
    assert "014525225" in issues[0].message


def test_invalid_bik_reported_once_when_both_rules_present():
    s = schema(
        [{"name": "account", "type": "string"}, {"name": "bik", "type": "string", "rules": [{"kind": "bik"}]}],
        [ACC_RULE],
    )
    issues = evaluate(s, {"account": ACCOUNT, "bik": "014525225"}, today=TODAY)
    assert codes(issues) == [("bik", "checksum")]


def test_decide_status():
    assert decide_status([]) == "ok"
    assert decide_status([Issue("a", "checksum", "m")]) == "needs_review"


# --- total_consistent ---------------------------------------------------------

TOTAL_RULE = {
    "kind": "total_consistent",
    "params": {"total": "total", "subtotal": "subtotal", "vat": "vat", "table": "items", "column": "amount"},
}
PAYABLE = schema(
    [ITEMS] + [{"name": n, "type": "number"} for n in ("subtotal", "vat", "total")],
    [TOTAL_RULE],
)


def test_total_consistent_vat_on_top():
    data = {"items": rows((10, 350, 3500), (1, 500, 500)), "subtotal": 4000, "vat": 800, "total": 4800}
    assert evaluate(PAYABLE, data, today=TODAY) == []


def test_total_consistent_vat_included():
    # «в т.ч. НДС»: подытог уже с НДС, итог к оплате равен ему
    data = {"items": rows((1, 1200, 1200)), "subtotal": 1200, "vat": 200, "total": 1200}
    assert evaluate(PAYABLE, data, today=TODAY) == []


def test_total_consistent_without_subtotal_uses_items():
    data = {"items": rows((10, 350, 3500), (1, 500, 500)), "subtotal": None, "vat": 800, "total": 4800}
    assert evaluate(PAYABLE, data, today=TODAY) == []
    issues = evaluate(PAYABLE, {**data, "total": 5800}, today=TODAY)
    assert codes(issues) == [("total", "sum_mismatch")]
    assert "сумма строк 4000" in issues[0].message


def test_total_consistent_mismatch():
    data = {"items": rows((1, 4000, 4000)), "subtotal": 4000, "vat": 800, "total": 4900}
    issues = evaluate(PAYABLE, data, today=TODAY)
    assert codes(issues) == [("total", "sum_mismatch")]
    assert "4900" in issues[0].message and "4000" in issues[0].message and "4800" in issues[0].message
    # без НДС: итог обязан совпасть с подытогом
    no_vat = evaluate(PAYABLE, {**data, "vat": None, "total": 4800}, today=TODAY)
    assert codes(no_vat) == [("total", "sum_mismatch")]
    # в пределах допуска — не расхождение
    assert evaluate(PAYABLE, {**data, "total": 4800.004}, today=TODAY) == []


def test_total_consistent_skips_without_base():
    assert evaluate(PAYABLE, {"items": rows((1, 1, 1)), "subtotal": 1, "vat": None, "total": None}, today=TODAY) == []
    # нет подытога, а в таблице пустая ячейка — по неполной сумме не судим
    data = {"items": rows((1, 10, 10), (1, 5, None)), "subtotal": None, "vat": None, "total": 999}
    assert evaluate(PAYABLE, data, today=TODAY) == []
    assert evaluate(PAYABLE, {"items": [], "subtotal": None, "vat": None, "total": 999}, today=TODAY) == []


def test_total_consistent_minimal_params():
    s = schema(
        [{"name": "subtotal", "type": "number"}, {"name": "total", "type": "number"}],
        [{"kind": "total_consistent", "params": {"total": "total", "subtotal": "subtotal"}}],
    )
    assert evaluate(s, {"subtotal": 10, "total": 10}, today=TODAY) == []
    assert codes(evaluate(s, {"subtotal": 10, "total": 12}, today=TODAY)) == [("total", "sum_mismatch")]


PRODUCT_SUM_RULE = {
    "kind": "table_product_sum",
    "params": {"table": "works", "qty": "qty", "price": "price", "total": "total"},
}
ORDER = schema(
    [
        {
            "name": "works",
            "type": "table",
            "columns": [{"name": "qty", "type": "number"}, {"name": "price", "type": "number"}],
        },
        {"name": "total", "type": "number"},
    ],
    [PRODUCT_SUM_RULE],
)


def test_product_sum_matches():
    data = {"works": [{"qty": 2, "price": 9000}, {"qty": 1, "price": 2500}], "total": 20500}
    assert evaluate(ORDER, data, today=TODAY) == []


def test_product_sum_mismatch():
    # заказ-наряд: 3 зуба, а в графе «Ед.» 1 — итого 4 500 не сходится с 1 × 1 500
    issues = evaluate(ORDER, {"works": [{"qty": 1, "price": 1500}], "total": 4500}, today=TODAY)
    assert codes(issues) == [("total", "sum_mismatch")]
    assert "4500" in issues[0].message and "1500" in issues[0].message


def test_product_sum_skipped_when_incomplete():
    # неполная строка дала бы ложное расхождение; пропуск в ней подсветит missing/uncertain
    assert evaluate(ORDER, {"works": [{"qty": None, "price": 1500}], "total": 4500}, today=TODAY) == []
    assert evaluate(ORDER, {"works": [], "total": 4500}, today=TODAY) == []
    assert evaluate(ORDER, {"works": [{"qty": 1, "price": 1500}], "total": None}, today=TODAY) == []


DATES = schema(
    [
        {"name": "issued", "type": "date", "rules": [{"kind": "date_not_older", "params": {"days": 120}}]},
        {"name": "delivery", "type": "date"},
    ],
    [{"kind": "date_within", "params": {"earlier": "issued", "later": "delivery", "max_days": 60}}],
)


def test_date_not_older():
    assert evaluate(DATES, {"issued": "2026-07-01"}, today=TODAY) == []
    issues = evaluate(DATES, {"issued": "2016-10-22"}, today=TODAY)
    assert codes(issues) == [("issued", "date_too_old")]
    assert "2016-10-22" in issues[0].message


def test_date_within():
    assert evaluate(DATES, {"issued": "2026-10-22", "delivery": "2026-11-03"}, today=TODAY) == []
    issues = evaluate(DATES, {"issued": "2026-10-22", "delivery": "2400-10-24"}, today=TODAY)
    assert codes(issues) == [("delivery", "date_range")]
    # обратный порядок — забота date_order, а не date_within
    assert evaluate(DATES, {"issued": "2026-10-22", "delivery": "2026-10-01"}, today=TODAY) == []
    assert evaluate(DATES, {"issued": "2026-10-22", "delivery": None}, today=TODAY) == []


def test_dictionary_rule_is_applied_outside_evaluate():
    # сверка со справочником идёт в resolve_dictionaries (нужны данные тенанта), evaluate её пропускает
    s = schema([{"name": "clinic", "type": "string", "rules": [{"kind": "dictionary", "params": {"name": "c"}}]}])
    assert evaluate(s, {"clinic": "что угодно"}, today=TODAY) == []


def test_corrected_is_informational():
    assert decide_status([Issue("clinic", "corrected", "…")]) == "ok"
    assert decide_status([Issue("clinic", "corrected", "…"), Issue("x", "uncertain", "…")]) == "needs_review"
