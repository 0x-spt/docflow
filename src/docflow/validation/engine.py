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
    if account is None or bik is None or not checksums.valid_bik(str(bik)):
        return []  # неверный БИК уже отмечен правилом bik на своём поле
    if checksums.valid_account(str(account), str(bik)):
        return []
    return [Issue(p["account"], "checksum", f"счёт «{account}» не сходится с БИК «{bik}» по контрольному ключу")]


DOC_REGISTRY: dict[str, DocRule] = {
    "table_sum_equals": _table_sum_equals,
    "table_row_product": _table_row_product,
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
    return issues


def decide_status(issues: list[Issue]) -> Status:
    return "needs_review" if issues else "ok"
