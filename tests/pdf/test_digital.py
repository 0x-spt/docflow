from datetime import UTC, datetime

import pypdfium2 as pdfium
import pytest

from docflow.pdf.digital import format_value, render_digital_pdf
from docflow.schemas.library import builtin_schemas
from docflow.schemas.model import ColumnSpec, DocSchema

CREATED = datetime(2026, 3, 5, 10, 0, tzinfo=UTC)


def _spec(type_: str) -> ColumnSpec:
    return ColumnSpec(name="x", type=type_)


def test_format_value_number():
    assert format_value(_spec("number"), 12500.5) == "12 500,50"
    assert format_value(_spec("number"), 3500.0) == "3 500"
    assert format_value(_spec("number"), 2.5) == "2,50"


def test_format_value_integer():
    assert format_value(_spec("integer"), 1234567) == "1 234 567"
    assert format_value(_spec("integer"), 12.0) == "12"


def test_format_value_date():
    assert format_value(_spec("date"), "2026-03-05") == "05.03.2026"
    assert format_value(_spec("date"), "не дата") == "не дата"


def test_format_value_bool():
    assert format_value(_spec("boolean"), True) == "да"
    assert format_value(_spec("boolean"), False) == "нет"


@pytest.mark.parametrize("value", [None, "", "  ", [], {}])
def test_format_value_empty(value):
    assert format_value(_spec("string"), value) == "—"


def _text(pdf: bytes) -> str:
    doc = pdfium.PdfDocument(pdf)
    return "\n".join(doc[i].get_textpage().get_text_range() for i in range(len(doc)))


def _invoice_data(rows=1):
    items = [
        {"name": f"Бумага офисная А4 №{i}", "qty": 10, "unit": "шт", "price": 350.0, "amount": 3500.0}
        for i in range(rows)
    ]
    return {"number": "15", "issued": "2026-03-05", "supplier": "ООО «Ромашка»", "total": 12500.5, "items": items}


def test_render_digital_pdf_contains_text(digital_font):
    schema = builtin_schemas()["invoice_ru"]
    pdf = render_digital_pdf(schema, _invoice_data(), status="ok", created_at=CREATED)
    assert pdf.startswith(b"%PDF")
    text = _text(pdf)
    assert "Счёт на оплату" in text
    assert "№ 15 от 05.03.2026" in text
    assert "Поставщик" in text and "ООО «Ромашка»" in text
    assert "12 500,50" in text
    assert "Бумага офисная А4 №0" in text
    assert "Цифровая версия, сформирована DocFlow по скану от 05.03.2026. Статус: проверен" in text
    assert "стр. 1 из 1" in text


def test_status_texts(digital_font):
    schema = builtin_schemas()["invoice_ru"]
    pdf = render_digital_pdf(schema, _invoice_data(), status="needs_review", created_at=CREATED, reviewed=True)
    assert "Статус: требует проверки, подтверждён оператором" in _text(pdf)


def test_render_digital_pdf_handles_nulls_and_long_values(digital_font):
    schema = builtin_schemas()["invoice_ru"]
    data = {f.name: None for f in schema.fields}
    data |= {"supplier": "Очень длинное название " * 10, "items": [], "_uncertain": ["total"]}
    pdf = render_digital_pdf(schema, data, status="needs_review", created_at=CREATED)
    text = _text(pdf)
    assert "—" in text and "_uncertain" not in text
    assert text.count("название") >= 10  # длинное значение перенесено, не обрезано

    big = render_digital_pdf(schema, _invoice_data(rows=120), status="ok", created_at=CREATED)
    doc = pdfium.PdfDocument(big)
    assert len(doc) > 1
    full = _text(big)
    assert "Бумага офисная А4 №119" in full
    assert f"стр. {len(doc)} из {len(doc)}" in full
    assert full.count("Наименование") >= 2  # шапка таблицы повторяется на новой странице


def test_render_very_long_unbroken_value(digital_font):
    schema = builtin_schemas()["invoice_ru"]
    data = _invoice_data() | {"supplier": "Я" * 200}
    data["items"][0]["name"] = "Ы" * 200
    text = _text(render_digital_pdf(schema, data, status="ok", created_at=CREATED)).replace("\n", "").replace("\r", "")
    assert "Я" * 200 in text and "Ы" * 200 in text


def test_render_without_title_uses_name(digital_font):
    schema = DocSchema(slug="my_doc", title="Мой док", description="d", fields=[{"name": "foo_bar", "type": "string"}])
    assert "foo_bar" in _text(render_digital_pdf(schema, {"foo_bar": "знач"}, status="ok", created_at=CREATED))


def _long_schema():
    from docflow.schemas.model import FieldSpec

    return DocSchema(
        slug="long_test", version=1, title="Тест", description="т",
        fields=[
            FieldSpec(name="number", type="string", title="Номер"),
            FieldSpec(name="note", type="string", title="Примечание"),
            FieldSpec(name="rows", type="table", title="Строки", columns=[
                ColumnSpec(name="a", type="string", title="А"), ColumnSpec(name="b", type="string", title="Б")]),
        ],
    )


def test_long_grid_value_renders(digital_font):
    data = {"number": "1", "note": "слово " * 1000}
    text = _text(render_digital_pdf(_long_schema(), data, status="ok", created_at=CREATED))
    assert "Примечание" in text and "слово" in text


def test_long_unbroken_table_cell_renders(digital_font):
    data = {"number": "1", "rows": [{"a": "Ж" * 5000, "b": "ok"}]}
    text = _text(render_digital_pdf(_long_schema(), data, status="ok", created_at=CREATED))
    assert "ЖЖЖ" in text and "…" in text


def test_long_number_renders(digital_font):
    data = {"number": "Н" * 300}
    text = _text(render_digital_pdf(_long_schema(), data, status="ok", created_at=CREATED))
    assert "ННН" in text
