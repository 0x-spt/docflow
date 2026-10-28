"""Excel-реестр документов: листы «Документы» и «Позиции» с теми же колонками, что и в Google Таблицах."""

import io
from collections.abc import Iterable
from datetime import date

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

from docflow.connectors.base import ExportDoc
from docflow.schemas.model import DocSchema
from docflow.tabular import DOCS_SHEET, ITEMS_SHEET, clean_text, doc_row, docs_header, item_rows, items_header

MAX_WIDTH = 60
MIN_WIDTH = 10
DATE_FORMAT = "DD.MM.YYYY"


def _width(header: str, column: list) -> int:
    longest = max([len(header), *(len(str(v)) for v in column if v is not None)])
    return max(MIN_WIDTH, min(MAX_WIDTH, longest + 2))


def _value(ws, value):
    if isinstance(value, str):
        c = WriteOnlyCell(ws, value=value)
        c.data_type = "s"  # строка остаётся строкой, даже если начинается с «=»
        return c
    if isinstance(value, date):
        c = WriteOnlyCell(ws, value=value)
        c.number_format = DATE_FORMAT
        return c
    return value


def _fill(ws, header: list[str], rows: list[list]) -> None:
    for i, title in enumerate(header, start=1):
        ws.column_dimensions[get_column_letter(i)].width = _width(title, [r[i - 1] for r in rows])
    ws.freeze_panes = "A2"
    cells = []
    for title in header:
        c = WriteOnlyCell(ws, value=clean_text(title))
        c.data_type = "s"
        c.font = Font(bold=True)
        cells.append(c)
    ws.append(cells)
    for row in rows:
        ws.append([_value(ws, v) for v in row])


def build_registry(schema: DocSchema, docs: Iterable[ExportDoc]) -> bytes:
    """xlsx-файл реестра. schema — заголовки; данные берутся из самих документов."""
    docs = list(docs)
    wb = Workbook(write_only=True)
    _fill(wb.create_sheet(DOCS_SHEET), docs_header(schema), [doc_row(d, target="xlsx") for d in docs])
    _fill(
        wb.create_sheet(ITEMS_SHEET),
        items_header(schema),
        [row for d in docs for row in item_rows(d, target="xlsx")],
    )
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
