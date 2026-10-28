"""Документ как таблица: колонки и строки для Google Таблиц и Excel-реестра (одни и те же)."""

from collections.abc import Iterator
from datetime import date
from typing import Any

from docflow.connectors.base import ExportDoc
from docflow.schemas.model import ColumnSpec, DocSchema, FieldSpec

DOCS_SHEET = "Документы"
ITEMS_SHEET = "Позиции"
ID_HEADER = "ID документа"
STATUS_LABELS = {"ok": "Проверен", "needs_review": "Нужна проверка"}

Cell = str | int | float | date | None


def label(spec: ColumnSpec) -> str:
    return spec.title or spec.name


def table_field(schema: DocSchema) -> FieldSpec | None:
    return next((f for f in schema.fields if f.type == "table"), None)


def doc_fields(schema: DocSchema, mapping: dict[str, str] | None = None) -> list[tuple[ColumnSpec, str]]:
    """Поля документа с заголовками колонок. Маппинг не пуст — только перечисленные, в его порядке."""
    flat = {f.name: f for f in schema.fields if f.type != "table"}
    if mapping:
        return [
            (flat[name], str(title).strip() or label(flat[name])) for name, title in mapping.items() if name in flat
        ]
    return [(f, label(f)) for f in flat.values()]


def docs_header(schema: DocSchema, mapping: dict[str, str] | None = None) -> list[str]:
    fields = [title for _, title in doc_fields(schema, mapping)]
    return [ID_HEADER, "Тип", "Номер", "Дата", *fields, "Статус", "Цифровая версия (PDF)", "Кабинет"]


def items_header(schema: DocSchema) -> list[str]:
    table = table_field(schema)
    return [ID_HEADER, "Номер", *(label(c) for c in (table.columns or []) if table)]


def cell(spec: ColumnSpec, value: Any, *, dates_as_date: bool) -> Cell:
    """Числа остаются числами, даты — датами (или строкой ДД.ММ.ГГГГ), пустое — None."""
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        return None
    if isinstance(value, str) and not value.strip():
        return None
    if spec.type == "boolean" and isinstance(value, bool):
        return "да" if value else "нет"
    if spec.type in ("number", "integer") and isinstance(value, (int, float)) and not isinstance(value, bool):
        return int(value) if float(value).is_integer() else value
    if spec.type == "date" and isinstance(value, str):
        try:
            d = date.fromisoformat(value)
        except ValueError:
            return value
        return d if dates_as_date else d.strftime("%d.%m.%Y")
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return value
    return str(value)


def doc_row(doc: ExportDoc, mapping: dict[str, str] | None = None, *, dates_as_date: bool = False) -> list[Cell]:
    issued: Cell = doc.issued if dates_as_date else (doc.issued.strftime("%d.%m.%Y") if doc.issued else None)
    fields = [
        cell(spec, doc.data.get(spec.name), dates_as_date=dates_as_date) for spec, _ in doc_fields(doc.schema, mapping)
    ]
    return [
        str(doc.id),
        doc.title,
        doc.number,
        issued,
        *fields,
        STATUS_LABELS.get(doc.status, doc.status),
        doc.digital_pdf_url,
        doc.cabinet_url,
    ]


def item_rows(doc: ExportDoc, *, dates_as_date: bool = False) -> Iterator[list[Cell]]:
    table = table_field(doc.schema)
    if table is None:
        return
    rows = doc.data.get(table.name)
    if not isinstance(rows, list):
        return
    for row in rows:
        if not isinstance(row, dict):
            continue
        yield [
            str(doc.id),
            doc.number,
            *(cell(c, row.get(c.name), dates_as_date=dates_as_date) for c in table.columns or []),
        ]
