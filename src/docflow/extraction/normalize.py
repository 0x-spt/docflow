"""Приведение ответа модели к типам полей. Неприводимое -> None + Issue(bad_value)."""
import math
import re
from datetime import date
from typing import Any

from docflow.schemas.llm import UNCERTAIN
from docflow.schemas.model import ColumnSpec, DocSchema
from docflow.validation.engine import Issue

_SPACES = re.compile(r"[\s   ]+")
_NUM = re.compile(r"[+-]?\d+(\.\d+)?")
_DMY = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4}|\d{2})$")
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
        # последний разделитель — десятичный, другой — разделитель тысяч
        dec, thou = (",", ".") if s.rfind(",") > s.rfind(".") else (".", ",")
        if s.count(dec) != 1:
            raise _Bad
        s = s.replace(thou, "").replace(dec, ".")
    elif "," in s:
        if s.count(",") != 1:
            raise _Bad  # «1,234,567» неоднозначно
        s = s.replace(",", ".")
    if not _NUM.fullmatch(s):
        raise _Bad
    return float(s)


def _parse_int(v: Any) -> int:
    f = _parse_number(v)
    if f != int(f):
        raise _Bad
    return int(f)


def _parse_date(v: Any) -> str:
    if not isinstance(v, str):
        raise _Bad
    s = v.strip()
    try:
        if m := _DMY.match(s):
            d, mo, y = int(m[1]), int(m[2]), m[3]
            if len(y) == 2:
                cur = date.today().year % 100
                yy = int(y)
                year = 2000 + yy if yy <= cur + 1 else 1900 + yy
            else:
                year = int(y)
            return date(year, mo, d).isoformat()
        if _ISO.fullmatch(s):
            return date.fromisoformat(s).isoformat()
    except ValueError as e:
        raise _Bad from e
    raise _Bad


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
    names = {f.name for f in schema.fields}
    unc = raw.get(UNCERTAIN)
    out[UNCERTAIN] = [n for n in unc if isinstance(n, str) and n in names] if isinstance(unc, list) else []
    return out, issues
