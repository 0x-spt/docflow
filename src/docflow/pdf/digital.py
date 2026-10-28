"""Цифровая версия документа: PDF, набранный заново по извлечённым данным (не скан)."""

import logging
from datetime import date, datetime
from pathlib import Path

from fpdf import FPDF
from fpdf.enums import Align, TableBordersLayout
from fpdf.fonts import FontFace

from docflow.config import get_settings
from docflow.schemas.model import ColumnSpec, DocSchema, FieldSpec

logger = logging.getLogger(__name__)

EMPTY = "—"
MARGIN_MM = 15
STATUS_TEXT = {"ok": "проверен", "needs_review": "требует проверки"}
_FONT = "DocFlow"
# поля, из которых собирается строка «№ … от …» под заголовком
_NUMBER, _ISSUED = "number", "issued"
_GRAY = (110, 110, 110)
_HEAD_FILL = (235, 235, 235)


def column_title(spec: ColumnSpec) -> str:
    return spec.title or spec.name


def _group(n: int) -> str:
    return f"{n:,}".replace(",", " ")


def format_value(spec: ColumnSpec, value) -> str:
    """Значение для людей: «12 500,50», ДД.ММ.ГГГГ, да/нет, пустое — «—»."""
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        return EMPTY
    if isinstance(value, str) and not value.strip():
        return EMPTY
    if spec.type == "boolean" and isinstance(value, bool):
        return "да" if value else "нет"
    if spec.type in ("number", "integer") and isinstance(value, (int, float)) and not isinstance(value, bool):
        if float(value).is_integer():
            return _group(int(value))
        whole, frac = f"{abs(value):.2f}".split(".")
        sign = "-" if value < 0 else ""
        return f"{sign}{_group(int(whole))},{frac}"
    if spec.type == "date" and isinstance(value, str):
        try:
            return date.fromisoformat(value).strftime("%d.%m.%Y")
        except ValueError:
            return value
    return str(value)


def _bold_path(regular: str) -> str | None:
    p = Path(regular)
    for name in (f"{p.stem}-Bold{p.suffix}", f"{p.stem}bd{p.suffix}", f"{p.stem}_bold{p.suffix}"):
        if (p.with_name(name)).exists():
            return str(p.with_name(name))
    return None


class _Pdf(FPDF):
    def __init__(self, service_line: str) -> None:
        super().__init__(orientation="P", unit="mm", format="A4")
        self.service_line = service_line
        self.set_margins(MARGIN_MM, MARGIN_MM, MARGIN_MM)
        self.set_auto_page_break(True, margin=MARGIN_MM + 6)
        self.set_title("DocFlow")
        self.set_creator("DocFlow")

    def footer(self) -> None:
        self.set_y(-(MARGIN_MM + 2))
        self.set_font(_FONT, "", 7)
        self.set_text_color(*_GRAY)
        page_w = self.w - 2 * MARGIN_MM
        self.cell(page_w - 25, 4, self.service_line, align=Align.L)
        self.cell(25, 4, f"стр. {self.page_no()} из {{nb}}", align=Align.R)
        self.set_text_color(0, 0, 0)


def _service_line(status: str, created_at: datetime, reviewed: bool) -> str:
    text = (
        f"Цифровая версия, сформирована DocFlow по скану от {created_at.strftime('%d.%m.%Y')}. "
        f"Статус: {STATUS_TEXT.get(status, status)}"
    )
    return text + (", подтверждён оператором" if reviewed else "")


def _is_empty(value) -> bool:
    return value is None or value == "" or value == [] or value == {}


def _header_line(schema: DocSchema, data: dict) -> str:
    by_name = {f.name: f for f in schema.fields}
    parts = []
    if _NUMBER in by_name and not _is_empty(data.get(_NUMBER)):
        parts.append(f"№ {format_value(by_name[_NUMBER], data[_NUMBER])}")
    if _ISSUED in by_name and not _is_empty(data.get(_ISSUED)):
        parts.append(f"от {format_value(by_name[_ISSUED], data[_ISSUED])}")
    return " ".join(parts)


def _draw_grid(pdf: _Pdf, fields: list[FieldSpec], data: dict) -> None:
    cells = [f"{column_title(f)}: {format_value(f, data.get(f.name))}" for f in fields]
    if not cells:
        return
    pdf.set_font(_FONT, "", 10)
    half = (pdf.w - 2 * MARGIN_MM) / 2
    with pdf.table(
        col_widths=(half, half),
        borders_layout=TableBordersLayout.NONE,
        first_row_as_headings=False,
        line_height=5.2,
        cell_fill_mode="NONE",
    ) as table:
        for i in range(0, len(cells), 2):
            row = table.row()
            row.cell(cells[i])
            row.cell(cells[i + 1] if i + 1 < len(cells) else "")
    pdf.ln(3)


def _column_widths(columns: list[ColumnSpec], rows: list[list[str]], total: float) -> list[float]:
    weights = []
    for i, col in enumerate(columns):
        longest = max([len(column_title(col))] + [len(r[i]) for r in rows[:200]])
        if col.type in ("string", "enum"):
            weights.append(min(max(longest, 14), 45))
        else:
            weights.append(min(max(longest, 7), 16))
    scale = total / sum(weights)
    return [w * scale for w in weights]


def _draw_table(pdf: _Pdf, field: FieldSpec, items) -> None:
    columns = field.columns or []
    pdf.set_font(_FONT, "B", 11)
    pdf.cell(0, 7, column_title(field), new_x="LMARGIN", new_y="NEXT")
    rows_in = [r for r in (items if isinstance(items, list) else []) if isinstance(r, dict)]
    if not rows_in:
        pdf.set_font(_FONT, "", 10)
        pdf.cell(0, 6, EMPTY, new_x="LMARGIN", new_y="NEXT")
        pdf.ln(2)
        return
    rows = [[format_value(c, r.get(c.name)) for c in columns] for r in rows_in]
    widths = _column_widths(columns, rows, pdf.w - 2 * MARGIN_MM)
    aligns = tuple("RIGHT" if c.type in ("number", "integer") else "LEFT" for c in columns)
    pdf.set_font(_FONT, "", 9)
    with pdf.table(
        col_widths=tuple(widths),
        text_align=aligns,
        line_height=4.8,
        padding=1,
        headings_style=FontFace(emphasis="BOLD", fill_color=_HEAD_FILL),
    ) as table:
        table.row([column_title(c) for c in columns])
        for r in rows:
            table.row(r)
    pdf.ln(3)


def render_digital_pdf(
    schema: DocSchema,
    data: dict,
    *,
    status: str,
    created_at: datetime,
    reviewed: bool = False,
    font_path: str | None = None,
) -> bytes:
    """A4-документ по схеме и данным. `reviewed` добавляет в служебную строку «подтверждён оператором»."""
    regular = font_path or get_settings().pdf_font_path
    pdf = _Pdf(_service_line(status, created_at, reviewed))
    pdf.add_font(_FONT, "", regular)
    pdf.add_font(_FONT, "B", _bold_path(regular) or regular)
    pdf.alias_nb_pages("{nb}")
    pdf.add_page()

    pdf.set_font(_FONT, "B", 16)
    pdf.multi_cell(0, 8, schema.title, new_x="LMARGIN", new_y="NEXT")
    line = _header_line(schema, data)
    if line:
        pdf.set_font(_FONT, "", 11)
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)

    # в строку под заголовком уходят только заполненные номер и дата; пустые остаются в сетке как «—»
    in_header = {n for n in (_NUMBER, _ISSUED) if not _is_empty(data.get(n))}
    plain = [f for f in schema.fields if f.type != "table" and f.name not in in_header]
    _draw_grid(pdf, plain, data)
    for f in schema.fields:
        if f.type == "table":
            _draw_table(pdf, f, data.get(f.name))
    return bytes(pdf.output())
