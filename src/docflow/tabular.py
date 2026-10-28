"""Документ как таблица: колонки и строки для Google Таблиц и Excel-реестра (одни и те же)."""

import re
from collections.abc import Iterator
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from docflow.schemas.model import ColumnSpec, DocSchema, FieldSpec

if TYPE_CHECKING:  # connectors.base подключает коннекторы, а те — этот модуль: циклический импорт только для типов
    from docflow.connectors.base import ExportDoc

Target = Literal["sheets", "xlsx"]

DOCS_SHEET = "Документы"
ITEMS_SHEET = "Позиции"
ID_HEADER = "ID документа"
STATUS_LABELS = {"ok": "Проверен", "needs_review": "Нужна проверка"}

Cell = str | int | float | date | None

SHEETS_EPOCH = date(1899, 12, 30)
_CONTROL = re.compile("[" + "".join(map(chr, [*range(10), *range(11, 32), 127])) + "]")  # всё, кроме \n
_FORMULA_START = ("=", "+", "-", "@")
# строка, которую Таблицы разберут сами как число/дату/время/булево: «00123», «40702810…», «05.10.2026», «TRUE»
_PARSED = re.compile(r"^[\d\s.,:/+\-eE%$]+$")


def clean_text(value: str) -> str:
    return _CONTROL.sub("", value)


def text(value: str, target: Target) -> str:
    """Строка из документа (недоверенный текст OCR) как текст, а не формула/число.

    Для Таблиц (USER_ENTERED) ставим `'` перед тем, что иначе стало бы формулой или было бы разобрано
    как число/дата; xlsx хранит строки с типом «s» (см. export_xlsx), там префикс не нужен.
    """
    value = clean_text(value)
    if target == "sheets" and value and (
        value.startswith(_FORMULA_START) or _PARSED.match(value) or value.upper() in ("TRUE", "FALSE")
    ):
        return "'" + value
    return value


def label(spec: ColumnSpec) -> str:
    return spec.title or spec.name


def table_field(schema: DocSchema) -> FieldSpec | None:
    return next((f for f in schema.fields if f.type == "table"), None)


def doc_fields(schema: DocSchema, mapping: dict[str, str] | None = None) -> list[tuple[ColumnSpec, str]]:
    """Поля документа с заголовками колонок. Маппинг не пуст — только перечисленные, в порядке схемы
    (порядок ключей маппинга не хранится: JSONB его не сохраняет)."""
    flat = {f.name: f for f in schema.fields if f.type != "table"}
    if mapping:
        return [
            (spec, str(mapping[name] or "").strip() or label(spec)) for name, spec in flat.items() if name in mapping
        ]
    return [(f, label(f)) for f in flat.values()]


def docs_header(schema: DocSchema, mapping: dict[str, str] | None = None) -> list[str]:
    fields = [title for _, title in doc_fields(schema, mapping)]
    return [ID_HEADER, "Тип", "Номер", "Дата", *fields, "Статус", "Цифровая версия (PDF)", "Кабинет"]


def items_header(schema: DocSchema) -> list[str]:
    table = table_field(schema)
    return [ID_HEADER, "Номер", *(label(c) for c in (table.columns or []) if table)]


def cell(spec: ColumnSpec, value: Any, *, target: Target) -> Cell:
    """Значение ячейки по типу поля: number/integer — числом, date — датой, прочее — всегда текстом.

    Дата для Таблиц — серийный номер (локаль не влияет; формат колонки ставится при создании шапки),
    для xlsx — настоящая дата. Пустое — None.
    """
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
            return text(value, target)
        return d if target == "xlsx" else (d - SHEETS_EPOCH).days
    return text(value if isinstance(value, str) else str(value), target)


def date_cell(value: date | None, target: Target) -> Cell:
    if value is None:
        return None
    return value if target == "xlsx" else (value - SHEETS_EPOCH).days


def doc_date_columns(schema: DocSchema, mapping: dict[str, str] | None = None) -> list[int]:
    """Индексы колонок листа документов с датами (для формата ДД.ММ.ГГГГ)."""
    return [3] + [4 + i for i, (spec, _) in enumerate(doc_fields(schema, mapping)) if spec.type == "date"]


def item_date_columns(schema: DocSchema) -> list[int]:
    table = table_field(schema)
    return [2 + i for i, c in enumerate(table.columns or []) if c.type == "date"] if table else []


def doc_row(doc: "ExportDoc", mapping: dict[str, str] | None = None, *, target: Target = "sheets") -> list[Cell]:
    fields = [cell(spec, doc.data.get(spec.name), target=target) for spec, _ in doc_fields(doc.schema, mapping)]
    return [
        text(str(doc.id), target),
        text(doc.title, target),
        text(doc.number, target) if doc.number else None,
        date_cell(doc.issued, target),
        *fields,
        STATUS_LABELS.get(doc.status, doc.status),
        text(doc.digital_pdf_url, target) if doc.digital_pdf_url else None,
        text(doc.cabinet_url, target) if doc.cabinet_url else None,
    ]


def item_rows(doc: "ExportDoc", *, target: Target = "sheets") -> Iterator[list[Cell]]:
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
            text(str(doc.id), target),
            text(doc.number, target) if doc.number else None,
            *(cell(c, row.get(c.name), target=target) for c in table.columns or []),
        ]
