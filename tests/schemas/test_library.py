import jsonschema

from docflow.schemas.library import builtin_schemas, load_schema
from docflow.schemas.llm import to_json_schema


def test_builtins_load():
    schemas = builtin_schemas()
    assert set(schemas) == {"invoice_ru", "upd_ru", "passport_ru"}
    for slug, s in schemas.items():
        assert s.slug == slug
        jsonschema.Draft202012Validator.check_schema(to_json_schema(s))


def test_load_schema_from_path(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("slug: xx\ntitle: T\ndescription: D\nfields:\n  - {name: a, type: string}\n", encoding="utf-8")
    assert load_schema(p).fields[0].name == "a"


def test_invoice_checks_payable_total():
    """Встроенный счёт сверяет «Всего к оплате» с подытогом и НДС, а не только подытог со строками."""
    from docflow.validation.engine import evaluate

    invoice = builtin_schemas()["invoice_ru"]
    assert "total_consistent" in {r.kind for r in invoice.rules}
    items = [{"name": "Бумага", "qty": 10, "unit": "шт", "price": 350, "amount": 3500}]
    data = {f.name: None for f in invoice.fields} | {"items": items, "subtotal": 3500, "vat": 700}
    codes = {(i.field, i.code) for i in evaluate(invoice, data | {"total": 4200})}
    assert ("total", "sum_mismatch") not in codes
    codes = {(i.field, i.code) for i in evaluate(invoice, data | {"total": 42000})}
    assert ("total", "sum_mismatch") in codes


def test_builtin_schemas_have_titles():
    """Для цифрового PDF у всех полей и колонок встроенных схем есть русская подпись."""
    for slug, s in builtin_schemas().items():
        for f in s.fields:
            assert f.title, f"{slug}.{f.name}: нет title"
            for c in f.columns or []:
                assert c.title, f"{slug}.{f.name}.{c.name}: нет title"


def test_upd_titles_fixed():
    fields = {f.name: f for f in builtin_schemas()["upd_ru"].fields}
    assert fields["vat"].title == "НДС"
    assert "Расчётный счёт" in fields["account"].description
    assert "Корреспондентский" not in fields["account"].description
