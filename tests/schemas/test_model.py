import pytest
from pydantic import ValidationError

from docflow.schemas.model import DocSchema, FieldSpec


def _schema(*fields, rules=()):
    return DocSchema(slug="doc_x", title="T", description="D", fields=list(fields), rules=list(rules))


def test_enum_requires_values():
    with pytest.raises(ValidationError):
        _schema(FieldSpec(name="x", type="enum"))


def test_table_requires_columns():
    with pytest.raises(ValidationError):
        _schema(FieldSpec(name="x", type="table"))


def test_duplicate_field_names_rejected():
    with pytest.raises(ValidationError):
        _schema(FieldSpec(name="a", type="string"), FieldSpec(name="a", type="integer"))


def test_bad_slug_rejected():
    with pytest.raises(ValidationError):
        DocSchema(slug="Bad Slug", title="T", description="D", fields=[])


def test_unknown_rule_rejected():
    with pytest.raises(ValidationError):
        _schema(FieldSpec(name="a", type="string", rules=[{"kind": "nope"}]))
    with pytest.raises(ValidationError):
        _schema(FieldSpec(name="a", type="string"), rules=[{"kind": "inn"}])  # полевое правило на уровне документа
    with pytest.raises(ValidationError):
        _schema(FieldSpec(name="a", type="string", rules=[{"kind": "date_order"}]))  # и наоборот


def test_known_rules_accepted():
    s = _schema(
        FieldSpec(name="a", type="string", rules=[{"kind": "inn"}]),
        FieldSpec(name="b", type="string"),
        rules=[{"kind": "date_order", "params": {"earlier": "a", "later": "b"}}],
    )
    assert s.fields[0].rules[0].kind == "inn"


def _tbl():
    return FieldSpec(
        name="items", type="table", columns=[{"name": "qty", "type": "number"}, {"name": "amount", "type": "number"}]
    )


def test_doc_rule_params_cross_checked():
    total = FieldSpec(name="total", type="number")
    ok = {"kind": "table_sum_equals", "params": {"table": "items", "column": "amount", "total": "total"}}
    assert _schema(_tbl(), total, rules=[ok])
    for bad in (
        {"table": "items", "column": "amout", "total": "total"},  # опечатка в колонке
        {"table": "itemz", "column": "amount", "total": "total"},  # нет такой таблицы
        {"table": "total", "column": "amount", "total": "total"},  # не таблица
        {"table": "items", "column": "amount", "total": "totl"},  # нет поля
        {"table": "items", "column": "amount"},  # параметр пропущен
    ):
        with pytest.raises(ValidationError):
            _schema(_tbl(), total, rules=[{"kind": "table_sum_equals", "params": bad}])
    with pytest.raises(ValidationError):
        params = {"table": "items", "qty": "qty", "price": "p", "amount": "amount"}
        _schema(_tbl(), rules=[{"kind": "table_row_product", "params": params}])
    with pytest.raises(ValidationError):
        _schema(total, rules=[{"kind": "date_order", "params": {"earlier": "total", "later": "nope"}}])
    with pytest.raises(ValidationError):
        _schema(total, rules=[{"kind": "account_matches_bik", "params": {"account": "total"}}])


def test_regex_rule_needs_valid_pattern():
    for params in ({}, {"pattern": 5}, {"pattern": "("}):
        with pytest.raises(ValidationError):
            _schema(FieldSpec(name="a", type="string", rules=[{"kind": "regex", "params": params}]))
    assert _schema(FieldSpec(name="a", type="string", rules=[{"kind": "regex", "params": {"pattern": r"^\d+$"}}]))
