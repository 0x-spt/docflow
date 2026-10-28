"""Маршрутизация документов: правила тенанта «если → то» над нормализованными данными.

Срабатывают все подходящие правила, их действия объединяются. Условия одного правила — через И.
Числа сравниваются как числа, даты как даты, строки — без учёта регистра. Неразборчивое значение условия
не совпадает ни с чем (правило молча не срабатывает), а не роняет конвейер.
"""

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.models import Dictionary, RoutingRules
from docflow.routing_model import Condition, Rule
from docflow.schemas.model import DocSchema
from docflow.validation.dictionaries import DictEntry, match

logger = logging.getLogger(__name__)

_TRUE = frozenset({"true", "1", "да", "yes", "истина"})
_FALSE = frozenset({"false", "0", "нет", "no", "ложь"})


@dataclass
class RoutingDecision:
    export_to: set[UUID] = field(default_factory=set)
    notify: set[UUID] = field(default_factory=set)
    # названия сработавших правил с «требовать согласования», по порядку, без повторов
    approval: list[str] = field(default_factory=list)


class _Bad(Exception):
    """Значение не приводится к типу поля."""


def parse_rules(raw: Any) -> list[Rule]:
    """Правила из routing_rules.rules; битое правило пропускается с предупреждением, а не роняет конвейер."""
    rules: list[Rule] = []
    for n, item in enumerate(raw if isinstance(raw, list) else []):
        try:
            rules.append(Rule.model_validate(item))
        except ValidationError:
            logger.warning("правило маршрутизации №%d не разобрано и пропущено", n + 1)
    return rules


def rule_dictionary_names(rules: Iterable[Rule]) -> set[str]:
    return {
        c.value for r in rules for c in r.conditions if c.op == "in_dictionary" and isinstance(c.value, str)
    }


async def fetch_dictionaries(
    session: AsyncSession, tenant_id: UUID, names: Iterable[str]
) -> dict[str, list[DictEntry]]:
    names = set(names)
    if not names:
        return {}
    rows = await session.execute(
        select(Dictionary.name, Dictionary.entries).where(Dictionary.tenant_id == tenant_id, Dictionary.name.in_(names))
    )
    return {
        name: [DictEntry(e["value"], tuple(e.get("aliases") or ())) for e in entries] for name, entries in rows.all()
    }


async def load_rules(session: AsyncSession, tenant_id: UUID) -> tuple[list[Rule], dict[str, list[DictEntry]]]:
    """Правила тенанта и справочники, на которые ссылаются их условия."""
    row = await session.get(RoutingRules, tenant_id)
    rules = parse_rules(row.rules) if row is not None else []
    return rules, await fetch_dictionaries(session, tenant_id, rule_dictionary_names(rules))


def _is_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, str):
        return not v.strip()
    if isinstance(v, list | dict):
        return not v
    return False


def _number(v: Any) -> float:
    if isinstance(v, bool):
        raise _Bad
    if isinstance(v, int | float):
        return float(v)
    if isinstance(v, str):
        s = v.replace(" ", "").replace(" ", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            raise _Bad from None
    raise _Bad


def _date(v: Any) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        s = v.strip()
        for fmt in ("%Y-%m-%d", "%d.%m.%Y"):
            try:
                return datetime.strptime(s, fmt).date()
            except ValueError:
                continue
    raise _Bad


def _plain(v: Any) -> str:
    """Число без хвоста «.0»: 7701234567.0 → «7701234567» (так его и пишут в строковых полях)."""
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)


def _bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = _plain(v).strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    raise _Bad


def _text(v: Any) -> str:
    if isinstance(v, list | dict):
        raise _Bad
    return _plain(v).strip().casefold()


def _kind(schema: DocSchema, name: str, value: Any) -> str:
    spec = next((f for f in schema.fields if f.name == name), None)
    if spec is not None:
        return {"integer": "number", "number": "number", "date": "date", "boolean": "boolean"}.get(spec.type, spec.type)
    # поле не из схемы (правило «для любой схемы»): судим по самому значению
    if isinstance(value, int | float) and not isinstance(value, bool):
        return "number"
    return "string"


_COERCE = {"number": _number, "date": _date, "boolean": _bool}
_ORDERED = {"number", "date"}


def _cells(value: Any) -> Iterable[str]:
    """Строки для «содержит»: само значение или все ячейки таблицы."""
    if isinstance(value, list):
        for row in value:
            if isinstance(row, dict):
                yield from (str(v) for v in row.values() if v is not None)
            elif row is not None:
                yield str(row)
    elif value is not None:
        yield str(value)


def _holds(
    c: Condition, schema: DocSchema, data: Mapping[str, Any], dictionaries: Mapping[str, Sequence[DictEntry]]
) -> bool:
    value = data.get(c.field)
    if c.op == "empty":
        return _is_empty(value)
    if c.op == "not_empty":
        return not _is_empty(value)
    if c.op == "contains":
        needle = "" if c.value is None else str(c.value).strip().casefold()
        return bool(needle) and any(needle in cell.casefold() for cell in _cells(value))
    if c.op == "in_dictionary":
        entries = dictionaries.get(c.value) if isinstance(c.value, str) else None
        if entries is None or not isinstance(value, str) or not value.strip():
            return False
        # только точное совпадение (без регистра и пунктуации): нечёткое отправило бы документ не туда
        return match(value, entries, fuzzy=False) is not None

    kind = _kind(schema, c.field, value)
    coerce = _COERCE.get(kind, _text)
    try:
        expected = coerce(c.value)
    except _Bad:
        return False  # неразборчивое условие ни с чем не совпадает
    if _is_empty(value):
        return c.op == "ne"
    try:
        actual = coerce(value)
    except _Bad:
        return c.op == "ne"  # мусор в числовом поле точно «не равен» числу
    if c.op == "eq":
        return actual == expected
    if c.op == "ne":
        return actual != expected
    if kind not in _ORDERED:
        return False
    return {
        "gt": actual > expected,
        "ge": actual >= expected,
        "lt": actual < expected,
        "le": actual <= expected,
    }[c.op]


def evaluate_rules(
    rules: Iterable[Rule],
    schema: DocSchema,
    data: Mapping[str, Any] | None,
    dictionaries: Mapping[str, Sequence[DictEntry]],
) -> RoutingDecision:
    data = data or {}
    decision = RoutingDecision()
    for rule in rules:
        if rule.schema_slug is not None and rule.schema_slug != schema.slug:
            continue
        if not all(_holds(c, schema, data, dictionaries) for c in rule.conditions):
            continue
        decision.export_to.update(rule.export_to)
        decision.notify.update(rule.notify)
        if rule.require_approval and rule.name not in decision.approval:
            decision.approval.append(rule.name)
    return decision
