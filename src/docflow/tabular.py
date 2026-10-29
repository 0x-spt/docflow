"""Документ как таблица: колонки и строки для Google Таблиц и Excel-реестра (одни и те же)."""

import re
from collections.abc import Iterator
from datetime import date
from typing import TYPE_CHECKING, Any, Literal

from docflow.fieldmatch import normalize
from docflow.labels import SERVICE_HEADERS
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
    columns = (table.columns or []) if table else []
    return [ID_HEADER, "Номер", *(label(c) for c in columns)]


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


# --- шапка, которая уже есть в таблице ---


def same_layout(existing: list[str], ours: list[str], *, lead: int, tail: int) -> bool:
    """Наша шапка с другими подписями: та же длина, служебные колонки (первые lead и последние tail) на местах."""
    existing = list(existing)
    while existing and not str(existing[-1] or "").strip():
        existing.pop()  # Таблицы не отдают пустые ячейки в конце, но и пустая строка — не колонка
    if len(existing) != len(ours):
        return False
    return existing[:lead] == ours[:lead] and existing[len(existing) - tail :] == ours[len(ours) - tail :]


def id_column(header: list[str]) -> int | None:
    """Индекс колонки «ID документа» в шапке клиента (после нормализации)."""
    wanted = normalize(ID_HEADER)
    return next((i for i, h in enumerate(header) if normalize(str(h or "")) == wanted), None)


def _variants(pairs: list[tuple[str, list[str | None]]]) -> dict[str, str]:
    """Название колонки (нормализованное) -> ключ значения. pairs — ключ и его варианты по ярусам: сначала
    первый вариант всех ключей, затем второй… — точное указание (маппинг, подпись) важнее штатного названия."""
    out: dict[str, str] = {}
    depth = max((len(v) for _, v in pairs), default=0)
    for tier in range(depth):
        for key, variants in pairs:
            if tier < len(variants) and variants[tier]:
                out.setdefault(normalize(str(variants[tier])), key)
    out.pop("", None)
    return out


def _align(
    header: list[str], names: dict[str, str], values: dict[str, Cell], dates: set[str]
) -> tuple[list[Cell], list[int]] | None:
    row: list[Cell] = [None] * len(header)
    date_idx: list[int] = []
    matched = False
    for i, h in enumerate(header):
        key = names.get(normalize(str(h or "")))
        if key is None:
            continue
        matched = True
        row[i] = values.get(key)
        if key in dates:
            date_idx.append(i)
    return (row, date_idx) if matched else None


def align_doc_row(
    doc: "ExportDoc", header: list[str], mapping: dict[str, str] | None, *, target: Target
) -> tuple[list[Cell], list[int]] | None:
    """Строка документа под шапку клиента: значение — в колонку, чьё название совпало (после normalize)
    со служебным, названием из маппинга, подписью компании, штатным названием или именем поля.

    Поля сопоставляются по имени (`name`), подпись — лишь один из вариантов названия. Вторым значением —
    индексы колонок с датами. None — не совпала ни одна колонка.
    """
    base = doc_row(doc, target=target)
    service = base[:4] + base[-3:]
    values: dict[str, Cell] = {f"@{h}": v for h, v in zip(SERVICE_HEADERS, service, strict=True)}
    names = {normalize(h): f"@{h}" for h in SERVICE_HEADERS}  # служебные — первыми
    dates = {"@Дата"}

    labeled = {f.name: f for f in doc.schema.fields}
    pairs: list[tuple[str, list[str | None]]] = []
    for f in doc.default_schema.fields:
        if f.type == "table":
            continue
        spec = labeled.get(f.name, f)
        mapped = str((mapping or {}).get(f.name) or "").strip() or None
        pairs.append((f.name, [mapped, spec.title, f.title or f.name, f.name]))
        values[f.name] = cell(spec, doc.data.get(f.name), target=target)
        if spec.type == "date":
            dates.add(f.name)
    for norm, key in _variants(pairs).items():
        names.setdefault(norm, key)
    return _align(header, names, values, dates)


def align_item_rows(
    doc: "ExportDoc", header: list[str], *, target: Target
) -> tuple[list[list[Cell]], list[int]] | None:
    """Строки позиций под шапку клиента: «ID документа», «Номер», затем колонки таблицы по названиям
    (подпись компании, штатное название, имя колонки). None — не совпала ни одна колонка."""
    table = table_field(doc.schema)
    columns = (table.columns or []) if table else []
    default_table = table_field(doc.default_schema)
    defaults = {c.name: c for c in (default_table.columns or [])} if default_table else {}
    pairs: list[tuple[str, list[str | None]]] = []
    for c in columns:
        d = defaults.get(c.name, c)
        pairs.append((c.name, [c.title, d.title or d.name, c.name]))
    names = {normalize(ID_HEADER): "@id", normalize("Номер"): "@number"}  # служебные — первыми
    for norm, key in _variants(pairs).items():
        names.setdefault(norm, key)
    dates = {c.name for c in columns if c.type == "date"}

    layout = _align(header, names, {}, dates)
    if layout is None:
        return None
    rows: list[list[Cell]] = []
    for item in item_rows(doc, target=target):
        values: dict[str, Cell] = {"@id": item[0], "@number": item[1]}
        values.update({c.name: v for c, v in zip(columns, item[2:], strict=True)})
        rows.append(_align(header, names, values, dates)[0])  # type: ignore[index]  # раскладка та же
    return rows, layout[1]
