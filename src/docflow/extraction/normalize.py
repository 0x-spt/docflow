"""Приведение ответа модели к типам полей. Неприводимое -> None + Issue(bad_value)."""
import math
import re
from dataclasses import dataclass
from datetime import date
from typing import Any

from docflow.schemas.llm import UNCERTAIN
from docflow.schemas.model import ColumnSpec, DocSchema
from docflow.validation.engine import Issue

_SPACES = re.compile(r"[\s   ]+")
_NUM = re.compile(r"[+-]?\d+(\.\d+)?")
_DMY = re.compile(r"^(\d{1,2})[./-](\d{1,2})(?:[./-](\d{4}|\d{2}))?$")
_D_MONTH_Y = re.compile(r"^(\d{1,2})\s*([а-я]+)\.?(?:\s*(\d{4}|\d{2}))?$")
_YEAR_SUFFIX = re.compile(r"\s*(?:г\.?|года?)$")
_MONTHS = {
    "янв": 1, "фев": 2, "мар": 3, "апр": 4, "май": 5, "мая": 5, "июн": 6,
    "июл": 7, "авг": 8, "сен": 9, "окт": 10, "ноя": 11, "дек": 12,
}  # fmt: skip
_DOT_THOUSANDS = re.compile(r"[+-]?\d{1,3}(\.\d{3})+")
_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")
_TRUE = {"true", "да"}
_FALSE = {"false", "нет"}


class _Bad(ValueError):
    pass


def _parse_number(v: Any) -> float:
    if isinstance(v, bool):
        raise _Bad
    if isinstance(v, int | float):
        if isinstance(v, float) and not math.isfinite(v):
            raise _Bad
        return float(v)
    if not isinstance(v, str):
        raise _Bad
    s = _SPACES.sub("", v)
    if "," in s and "." in s:
        # последний разделитель — десятичный, другой — разделитель тысяч и стоит только в целой части группами по 3
        dec, thou = (",", ".") if s.rfind(",") > s.rfind(".") else (".", ",")
        if s.count(dec) != 1 or not re.fullmatch(rf"[+-]?\d{{1,3}}(?:{re.escape(thou)}\d{{3}})*", s.split(dec)[0]):
            raise _Bad
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        if s.count(",") != 1:
            raise _Bad  # «1,234,567» неоднозначно
        s = s.replace(",", ".")
    elif _DOT_THOUSANDS.fullmatch(s):
        raise _Bad  # «12.500» — скорее всего двенадцать тысяч пятьсот, а не 12,5
    if not _NUM.fullmatch(s):
        raise _Bad
    return float(s)


def _parse_int(v: Any) -> int:
    f = _parse_number(v)
    if f != int(f):
        raise _Bad
    return int(f)


@dataclass(frozen=True)
class _NoYear:
    """Дата без года («24.10», «24 окт»): год подставляет normalize по дате документа."""

    day: int
    month: int


def _today() -> date:
    return date.today()


def _year(y: str) -> int:
    if len(y) == 4:
        return int(y)
    yy, cur = int(y), _today().year % 100
    return 2000 + yy if yy <= cur + 1 else 1900 + yy


def _month(word: str) -> int:
    for prefix, n in _MONTHS.items():
        if word.startswith(prefix):
            return n
    raise _Bad


def _parse_date(v: Any) -> str | _NoYear:
    """Дата как написана на документе → ISO; без года → _NoYear."""
    if not isinstance(v, str):
        raise _Bad
    s = _YEAR_SUFFIX.sub("", v.strip().lower().replace("ё", "е")).rstrip(".").strip()
    try:
        if _ISO.fullmatch(s):
            return date.fromisoformat(s).isoformat()
        if m := _DMY.match(s):
            d, mo, y = int(m[1]), int(m[2]), m[3]
        elif m := _D_MONTH_Y.match(s):
            d, mo, y = int(m[1]), _month(m[2]), m[3]
        else:
            raise _Bad
        if y is None:
            date(2000, mo, d)  # високосный: 29.02 допустим до подстановки года
            return _NoYear(d, mo)
        return date(_year(y), mo, d).isoformat()
    except ValueError as e:
        raise _Bad from e


def _parse_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    raise _Bad


def _parse_string(v: Any) -> str | None:
    if isinstance(v, bool) or not isinstance(v, str | int | float):
        raise _Bad
    s = str(v).strip()
    return s or None


def _scalar(spec: ColumnSpec, v: Any, path: str, issues: list[Issue]) -> Any:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        match spec.type:
            case "number":
                return _parse_number(v)
            case "integer":
                return _parse_int(v)
            case "date":
                return _parse_date(v)
            case "boolean":
                return _parse_bool(v)
            case "enum":
                s = v.strip() if isinstance(v, str) else None
                if s not in (spec.values or []):
                    raise _Bad
                return s
            case _:
                return _parse_string(v)
    except _Bad:
        issues.append(Issue(path, "bad_value", f"не удалось привести значение «{v}» к типу «{spec.type}»"))
        return None


def normalize(schema: DocSchema, raw: dict) -> tuple[dict, list[Issue]]:
    issues: list[Issue] = []
    out: dict[str, Any] = {}
    for f in schema.fields:
        v = raw.get(f.name)
        if f.type != "table":
            out[f.name] = _scalar(f, v, f.name, issues)
            continue
        rows: list[dict[str, Any]] = []
        if v is not None and not isinstance(v, list):
            issues.append(Issue(f.name, "bad_value", f"ожидалась таблица, получено «{v}»"))
            v = []
        for i, row in enumerate(v or []):
            if not isinstance(row, dict):
                issues.append(Issue(f"{f.name}[{i}]", "bad_value", f"строка таблицы не объект: «{row}»"))
                continue
            cells = {c.name: _scalar(c, row.get(c.name), f"{f.name}[{i}].{c.name}", issues) for c in f.columns or []}
            rows.append(cells)
        out[f.name] = rows
    _fill_years(schema, out, issues)
    out[UNCERTAIN] = _uncertain(schema, raw.get(UNCERTAIN), issues)
    return out, issues


def _fill_years(schema: DocSchema, out: dict[str, Any], issues: list[Issue]) -> None:
    """Год для дат без года — от первой полной даты документа (дата наряда, счёта), иначе от сегодня.

    Ближайший к опорной дате год: «03.01» в наряде от 28.12.2026 — это 2027-01-03.
    """
    anchor = next(
        (date.fromisoformat(out[f.name]) for f in schema.fields if f.type == "date" and isinstance(out[f.name], str)),
        None,
    ) or _today()
    cells: list[tuple[dict[str, Any], str, str]] = []
    for f in schema.fields:
        if f.type == "table":
            for i, row in enumerate(out[f.name]):
                cells += [(row, c, f"{f.name}[{i}].{c}") for c in row]
        else:
            cells.append((out, f.name, f.name))
    for holder, key, path in cells:
        v = holder[key]
        if not isinstance(v, _NoYear):
            continue
        holder[key] = None
        candidates = [date(anchor.year + k, v.month, v.day) for k in (-1, 0, 1) if _valid(anchor.year + k, v)]
        if not candidates:
            issues.append(Issue(path, "bad_value", f"не удалось привести значение «{v.day:02}.{v.month:02}» к дате"))
            continue
        holder[key] = min(candidates, key=lambda d: abs(d - anchor)).isoformat()


def _valid(year: int, v: _NoYear) -> bool:
    try:
        date(year, v.month, v.day)
        return True
    except ValueError:
        return False


_TOP_LEVEL = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:[\[.]|$)")


def _uncertain(schema: DocSchema, unc: Any, issues: list[Issue]) -> list[str]:
    """Список полей, в которых модель не уверена, — только имена верхнего уровня.

    Неуверенность модели нельзя молча терять: она переводит документ в needs_review. Поэтому всё, что не
    удалось сопоставить полю, не отбрасывается, а становится замечанием uncertain (fail closed).
    """
    if unc is None:
        return []  # ключа нет — модель ни в чём не сомневается
    if not isinstance(unc, list):
        issues.append(Issue(UNCERTAIN, "uncertain", f"модель вернула неуверенность не списком: «{unc}»"))
        return []
    names = {f.name for f in schema.fields}
    found: list[str] = []
    unknown: list[str] = []
    for entry in unc:
        # «items[0].price» или «items.price» — неуверенность внутри таблицы относится к полю items
        m = _TOP_LEVEL.match(entry) if isinstance(entry, str) else None
        name = m[1] if m else None
        if name in names:
            if name not in found:
                found.append(name)
        else:
            unknown.append(str(entry))
    if unknown:
        listed = ", ".join(f"«{u}»" for u in unknown)
        issues.append(Issue(None, "uncertain", f"модель не уверена в значениях, которых нет в схеме: {listed}"))
    return found
