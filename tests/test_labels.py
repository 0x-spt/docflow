import pytest

from docflow.fieldmatch import normalize
from docflow.labels import (
    LabelsInvalid,
    apply_labels,
    check_label_set,
    default_queue_fields,
    queue_fields_for,
)
from docflow.schemas.library import builtin_schemas
from docflow.schemas.llm import to_json_schema

SCHEMAS = builtin_schemas()
INV = SCHEMAS["invoice_ru"]
UPD = SCHEMAS["upd_ru"]
PASSPORT = SCHEMAS["passport_ru"]


def _column(schema, table, name):
    return next(c for f in schema.fields if f.name == table for c in f.columns if c.name == name)


def _field(schema, name):
    return next(f for f in schema.fields if f.name == name)


def test_apply_labels_replaces_titles_and_keeps_source():
    before = to_json_schema(INV)
    dump = INV.model_dump()
    out = apply_labels(INV, {"vat": "Налог", "items.qty": "Кол-во шт"})
    assert _field(out, "vat").title == "Налог"
    assert _column(out, "items", "qty").title == "Кол-во шт"
    assert INV.model_dump() == dump
    assert _field(INV, "vat").title != "Налог"
    assert to_json_schema(INV) == before
    assert to_json_schema(out) == before


def test_stale_label_keys_ignored():
    out = apply_labels(INV, {"gone": "X", "vat": "  "})
    assert out.model_dump() == INV.model_dump()


def test_check_rejects_bad_values():
    with pytest.raises(LabelsInvalid) as exc:
        check_label_set(INV, {"vat": "x" * 81, "total": "a\nb", "нет_поля": "Что-то"}, [])
    assert exc.value.errors == [
        (["labels", "vat"], "не длиннее 80 символов"),
        (["labels", "total"], "без управляющих символов"),
        (["labels", "нет_поля"], "нет такого поля в схеме"),
    ]


def test_check_duplicate_case_insensitive():
    with pytest.raises(LabelsInvalid) as exc:
        check_label_set(INV, {"vat": "всего к оплате"}, [])
    assert exc.value.errors == [(["labels", "vat"], "такое название уже у поля «Всего к оплате»")]
    with pytest.raises(LabelsInvalid):
        check_label_set(INV, {"vat": "Налог", "total": "налог"}, [])


def test_check_columns_duplicate_within_table():
    with pytest.raises(LabelsInvalid):
        check_label_set(INV, {"items.qty": "цена"}, [])


def test_normalize():
    assert normalize("  Всё — ИТОГО: ") == "все — итого"
    assert normalize("«ИНН»") == "инн"
    assert normalize("Сумма  к  оплате") == "сумма к оплате"


def test_label_cannot_take_service_column():
    with pytest.raises(LabelsInvalid) as exc:
        check_label_set(INV, {"vat": "Статус"}, [])
    assert exc.value.errors == [(["labels", "vat"], "так называется служебная колонка таблиц")]
    ok = check_label_set(INV, {"number": "Номер"}, [])
    assert ok.labels == {"number": "Номер"}


def test_check_cleans_values():
    out = check_label_set(INV, {"vat": "  Налог ", "total": "   "}, [])
    assert out.labels == {"vat": "Налог"}


def test_queue_fields_rules():
    with pytest.raises(LabelsInvalid) as exc:
        check_label_set(INV, {}, ["supplier", "buyer", "total", "vat"])
    assert exc.value.errors == [(["queue_fields"], "в очереди не больше 3 полей")]
    with pytest.raises(LabelsInvalid) as exc:
        check_label_set(INV, {}, ["items"])
    assert exc.value.errors == [(["queue_fields"], "в очередь можно только поле верхнего уровня: «items»")]
    assert check_label_set(INV, {}, ["total", "total"]).queue_fields == ["total"]


def test_default_queue_fields():
    assert default_queue_fields(INV) == ["supplier", "total"]
    assert default_queue_fields(UPD) == ["supplier", "total"]
    assert default_queue_fields(PASSPORT) == ["full_name", "birth_date"]


def test_queue_fields_for_falls_back():
    assert queue_fields_for(INV, ["gone"]) == default_queue_fields(INV)
    assert queue_fields_for(INV, ["vat", "gone"]) == ["vat"]
