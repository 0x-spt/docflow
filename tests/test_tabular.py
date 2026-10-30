import subprocess
import sys


def test_tabular_imports_first_in_fresh_interpreter():
    """Раньше `import docflow.tabular` падал циклическим импортом через connectors."""
    done = subprocess.run([sys.executable, "-c", "import docflow.tabular"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_doc_fields_follow_schema_order_not_mapping_order():
    from docflow.schemas.model import DocSchema
    from docflow.tabular import doc_fields, docs_header

    schema = DocSchema(
        slug="abc",
        title="T",
        description="d",
        fields=[
            {"name": "a", "type": "string", "title": "Альфа"},
            {"name": "b", "type": "string"},
            {"name": "c", "type": "string"},
        ],
    )
    # порядок ключей маппинга другой (JSONB его не хранит), пустая колонка - название по умолчанию
    mapping = {"c": "Колонка C", "a": "", "zzz": "нет такого поля"}
    assert [(s.name, t) for s, t in doc_fields(schema, mapping)] == [("a", "Альфа"), ("c", "Колонка C")]
    assert docs_header(schema, mapping)[4:6] == ["Альфа", "Колонка C"]


def _invoice_doc(labels=None):
    import uuid
    from datetime import UTC, date, datetime

    from docflow.connectors.base import ExportDoc
    from docflow.labels import apply_labels
    from docflow.schemas.library import builtin_schemas

    base = builtin_schemas()["invoice_ru"]
    return ExportDoc(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        tenant_id=uuid.uuid4(),
        schema=apply_labels(base, labels) if labels else base,
        base_schema=base if labels else None,
        data={"number": "42", "issued": "2026-10-05", "supplier": "ООО Ромашка", "total": 1250.5, "vat": 250},
        status="ok",
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        title=base.title,
        number="42",
        issued=date(2026, 10, 5),
    )


def test_align_by_label_default_name_and_mapping():
    from docflow.tabular import align_doc_row

    header = ["Сумма к оплате", "vat", "Поставщик", "Дата"]
    row, dates = align_doc_row(_invoice_doc(), header, {"total": "Сумма к оплате"}, target="sheets")
    assert row == [1250.5, 250, "ООО Ромашка", 46300]
    assert dates == [3]


def test_align_uses_company_label():
    from docflow.tabular import align_doc_row

    row, _ = align_doc_row(_invoice_doc({"vat": "Налог"}), ["налог "], {}, target="sheets")
    assert row == [250]


def test_align_blank_and_duplicate_headers():
    from docflow.tabular import align_doc_row

    row, _ = align_doc_row(_invoice_doc(), ["", "Поставщик", "поставщик", "Мусор", ""], None, target="sheets")
    assert row == [None, "ООО Ромашка", "ООО Ромашка", None, None]


def test_align_none_when_nothing_matches():
    from docflow.tabular import align_doc_row, align_item_rows

    assert align_doc_row(_invoice_doc(), ["А", "Б"], {}, target="sheets") is None
    assert align_item_rows(_invoice_doc(), ["А", "Б"], target="sheets") is None


def test_align_item_rows_by_names():
    from docflow.tabular import align_item_rows

    doc = _invoice_doc({"items.qty": "Штук"})
    doc.data["items"] = [{"name": "Болт", "qty": 2}]
    rows, dates = align_item_rows(doc, ["Наименование", "штук", "ID документа", "x"], target="sheets")
    assert rows == [["Болт", 2, "'11111111-1111-1111-1111-111111111111", None]] and dates == []


def test_same_layout():
    from docflow.schemas.library import builtin_schemas
    from docflow.tabular import docs_header, same_layout

    schema = builtin_schemas()["invoice_ru"]
    ours = docs_header(schema)
    renamed = [*ours]
    renamed[5] = "Налог"
    assert same_layout(renamed, ours, lead=4, tail=3)
    assert same_layout([*renamed, "", ""], ours, lead=4, tail=3)  # пустые ячейки в конце — не колонки
    assert not same_layout([*ours, "Ещё"], ours, lead=4, tail=3)
    shifted = [*ours]
    shifted[3], shifted[4] = shifted[4], shifted[3]
    assert not same_layout(shifted, ours, lead=4, tail=3)
    assert not same_layout(ours[:-1] + ["Кабинет клиента"], ours, lead=4, tail=3)


def test_id_column():
    from docflow.tabular import id_column

    assert id_column(["Поставщик", " id ДОКУМЕНТА."]) == 1
    assert id_column(["Поставщик"]) is None
