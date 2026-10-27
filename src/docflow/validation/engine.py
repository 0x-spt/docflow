"""Движок проверок. Схема фиксирует то, что НАПИСАНО; здесь решается, можно ли данным верить.

Входные данные уже нормализованы: числа — int/float, даты — ISO «YYYY-MM-DD», таблицы — списки словарей,
отсутствующее значение — None. Правило, которому не хватает входных данных, молча пропускается
(кроме missing_required): отсутствие значения — не ошибка проверяемого реквизита.
"""
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from docflow.schemas.llm import UNCERTAIN
from docflow.schemas.model import DOC_RULES, FIELD_RULES, DocSchema, FieldSpec
from docflow.validation import checksums

Status = Literal["ok", "needs_review", "failed"]

_EPS = 1e-9


@dataclass(frozen=True)
class Issue:
    field: str | None
    code: str
    message: str


def _num(x: float) -> str:
    return f"{x:.2f}".rstrip("0").rstrip(".")


def _is_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, list) and not v)


def _parse_date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v))
    except ValueError:
        return None


# --- правила поля: (имя поля, значение != None, параметры, today) -> issues ---

FieldRule = Callable[[str, Any, dict[str, Any], date], list[Issue]]


def _checksum_rule(label: str, check: Callable[[str], bool]) -> FieldRule:
    def rule(name: str, value: Any, params: dict[str, Any], today: date) -> list[Issue]:
        if check(str(value)):
            return []
        return [Issue(name, "checksum", f"{label} «{value}» не проходит проверку контрольной суммы")]

    return rule


def _regex_rule(name: str, value: Any, params: dict[str, Any], today: date) -> list[Issue]:
    if re.search(params["pattern"], str(value)):
        return []
    return [Issue(name, "pattern", f"значение «{value}» не соответствует формату {params['pattern']}")]


def _date_not_future_rule(name: str, value: Any, params: dict[str, Any], today: date) -> list[Issue]:
    d = _parse_date(value)
    if d is None:
        return [Issue(name, "pattern", f"«{value}» не похоже на дату")]
    if d > today:
        return [Issue(name, "date_future", f"дата {d.isoformat()} позже сегодняшней ({today.isoformat()})")]
    return []


FIELD_REGISTRY: dict[str, FieldRule] = {
    "inn": _checksum_rule("ИНН", checksums.valid_inn),
    "kpp": _checksum_rule("КПП", checksums.valid_kpp),
    "ogrn": _checksum_rule("ОГРН", checksums.valid_ogrn),
    "snils": _checksum_rule("СНИЛС", checksums.valid_snils),
    "bik": _checksum_rule("БИК", checksums.valid_bik),
    "regex": _regex_rule,
    "date_not_future": _date_not_future_rule,
}


# --- правила документа: (данные, параметры) -> issues ---

DocRule = Callable[[dict[str, Any], dict[str, Any]], list[Issue]]


def _rows(data: dict[str, Any], table: str) -> list[dict[str, Any]]:
    rows = data.get(table)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def _table_sum_equals(data: dict[str, Any], p: dict[str, Any]) -> list[Issue]:
    total = data.get(p["total"])
    values = [r[p["column"]] for r in _rows(data, p["table"]) if r.get(p["column"]) is not None]
    if total is None or not values:
        return []
    tolerance = float(p.get("tolerance", 0.01))
    s = sum(values)
    if abs(s - total) > tolerance + _EPS:
        return [Issue(p["total"], "sum_mismatch", f"итого {_num(total)} ≠ сумма строк {_num(s)}")]
    return []


def _table_row_product(data: dict[str, Any], p: dict[str, Any]) -> list[Issue]:
    issues = []
    for i, row in enumerate(_rows(data, p["table"])):
        qty, price, amount = row.get(p["qty"]), row.get(p["price"]), row.get(p["amount"])
        if qty is None or price is None or amount is None:
            continue
        expected = qty * price
        if abs(expected - amount) > 0.01 + _EPS:
            issues.append(
                Issue(
                    f"{p['table']}[{i}].{p['amount']}",
                    "row_mismatch",
                    f"строка {i + 1}: {_num(qty)} × {_num(price)} = {_num(expected)}, а в сумме {_num(amount)}",
                )
            )
    return issues


def _table_product_sum(data: dict[str, Any], p: dict[str, Any]) -> list[Issue]:
    """Итог = Σ количество × цена, когда графы «сумма» в таблице нет (заказ-наряды, акты)."""
    total = data.get(p["total"])
    rows = _rows(data, p["table"])
    if not _is_number(total) or not rows:
        return []
    pairs = [(r.get(p["qty"]), r.get(p["price"])) for r in rows]
    if not all(_is_number(q) and _is_number(pr) for q, pr in pairs):
        return []  # неполная строка дала бы ложное расхождение
    s = sum(q * pr for q, pr in pairs)
    tolerance = float(p.get("tolerance", 0.01))
    if abs(s - total) > tolerance + _EPS:
        detail = " + ".join(f"{_num(q)} × {_num(pr)}" for q, pr in pairs)
        return [Issue(p["total"], "sum_mismatch", f"итого {_num(total)} ≠ {detail} = {_num(s)}")]
    return []


def _date_order(data: dict[str, Any], p: dict[str, Any]) -> list[Issue]:
    earlier, later = data.get(p["earlier"]), data.get(p["later"])
    if earlier is None or later is None:
        return []
    d_earlier, d_later = _parse_date(earlier), _parse_date(later)
    if d_earlier is None or d_later is None:
        name, raw = (p["earlier"], earlier) if d_earlier is None else (p["later"], later)
        return [Issue(name, "pattern", f"«{raw}» не похоже на дату")]
    if d_earlier > d_later:
        return [
            Issue(
                p["later"],
                "date_order",
                f"{p['later']} {d_later.isoformat()} раньше, чем {p['earlier']} {d_earlier.isoformat()}",
            )
        ]
    return []


def _account_matches_bik(data: dict[str, Any], p: dict[str, Any]) -> list[Issue]:
    account, bik = data.get(p["account"]), data.get(p["bik"])
    if account is None or bik is None:
        return []
    if not checksums.valid_bik(str(bik)):
        # без верного БИК р/с не проверить; сообщаем сами, не полагаясь на правило bik у поля
        return [Issue(p["bik"], "checksum", f"БИК «{bik}» некорректен, р/с «{account}» проверить нельзя")]
    if checksums.valid_account(str(account), str(bik)):
        return []
    return [Issue(p["account"], "checksum", f"счёт «{account}» не сходится с БИК «{bik}» по контрольному ключу")]


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _total_consistent(data: dict[str, Any], p: dict[str, Any]) -> list[Issue]:
    """Итог к оплате = подытог (НДС внутри) или подытог + НДС (НДС сверху).

    Подытог — поле subtotal, а без него сумма колонки таблицы, если все ячейки заполнены числами:
    неполная таблица дала бы ложное расхождение.
    """
    total = data.get(p["total"])
    if not _is_number(total):
        return []
    base: float | None = None
    base_label = "подытог"
    subtotal = data.get(p["subtotal"]) if p.get("subtotal") else None
    if _is_number(subtotal):
        base = float(subtotal)
    elif p.get("table") and p.get("column"):
        cells = [r.get(p["column"]) for r in _rows(data, p["table"])]
        if cells and all(_is_number(c) for c in cells):
            base = float(sum(cells))
            base_label = "сумма строк"
    if base is None:
        return []
    vat = data.get(p["vat"]) if p.get("vat") else None
    tolerance = float(p.get("tolerance", 0.01)) + _EPS
    if abs(total - base) <= tolerance:
        return []
    if _is_number(vat) and abs(total - (base + vat)) <= tolerance:
        return []
    if _is_number(vat):
        message = (
            f"итого к оплате {_num(total)} не сходится: {base_label} {_num(base)}, "
            f"с НДС {_num(vat)} — {_num(base + vat)}"
        )
    else:
        message = f"итого к оплате {_num(total)} ≠ {base_label} {_num(base)}"
    return [Issue(p["total"], "sum_mismatch", message)]


DOC_REGISTRY: dict[str, DocRule] = {
    "total_consistent": _total_consistent,
    "table_sum_equals": _table_sum_equals,
    "table_row_product": _table_row_product,
    "table_product_sum": _table_product_sum,
    "date_order": _date_order,
    "account_matches_bik": _account_matches_bik,
}

# Реестры обязаны совпадать со списком правил, принимаемых DocSchema (это проверяет тест).
KNOWN_RULES = FIELD_RULES | DOC_RULES


def _field_issues(f: FieldSpec, data: dict[str, Any], today: date) -> list[Issue]:
    value = data.get(f.name)
    if _is_empty(value):
        if f.required:
            return [Issue(f.name, "missing_required", f"обязательное поле «{f.name}» не найдено в документе")]
        return []
    issues: list[Issue] = []
    for r in f.rules:
        issues += FIELD_REGISTRY[r.kind](f.name, value, r.params, today)
    return issues


def evaluate(schema: DocSchema, data: dict[str, Any], *, today: date | None = None) -> list[Issue]:
    today = today or date.today()
    issues: list[Issue] = []
    for f in schema.fields:
        issues += _field_issues(f, data, today)
    for name in data.get(UNCERTAIN) or []:
        issues.append(Issue(str(name), "uncertain", f"модель не уверена в значении «{name}»"))
    for r in schema.rules:
        issues += DOC_REGISTRY[r.kind](data, r.params)
    return _dedupe(issues)


def _dedupe(issues: list[Issue]) -> list[Issue]:
    """Одно и то же (поле, код) не повторяем: полевое и документное правила могут ловить одно и то же."""
    seen: set[tuple[str | None, str]] = set()
    out = []
    for i in issues:
        if (i.field, i.code) not in seen:
            seen.add((i.field, i.code))
            out.append(i)
    return out


def decide_status(issues: list[Issue]) -> Status:
    return "needs_review" if issues else "ok"
