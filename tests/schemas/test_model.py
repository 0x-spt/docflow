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
        rules=[{"kind": "date_order", "params": {"earlier": "a", "later": "b"}}],
    )
    assert s.fields[0].rules[0].kind == "inn"
