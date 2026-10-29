"""Свои названия полей компании: проверка, наложение на схему, умолчание полей очереди.

Ключ подписи — имя поля верхнего уровня («vat») или «таблица.колонка» («items.qty»). `Document.data`,
промпт и JSON Schema остаются по служебным именам: подпись меняет только то, что видит человек.
"""

import uuid
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.models import FieldLabels
from docflow.fieldmatch import normalize
from docflow.schemas.model import DocSchema, FieldSpec

if TYPE_CHECKING:  # pipeline тянет много лишнего: нужен только для типа
    from docflow.pipeline import SchemaResolver

LABEL_MAX = 80
QUEUE_MAX = 3
# служебные колонки таблиц (Google Таблицы и Excel-реестр); «Номер» и «Дата» подписью занять можно —
# это колонки самих полей number/issued
SERVICE_HEADERS: tuple[str, ...] = (
    "ID документа",
    "Тип",
    "Номер",
    "Дата",
    "Статус",
    "Цифровая версия (PDF)",
    "Кабинет",
)
_SERVICE_RESERVED = frozenset(normalize(h) for h in SERVICE_HEADERS) - {normalize("Номер"), normalize("Дата")}

# правила поля, по которым поле — реквизит, а не название стороны или итог
_ID_RULES = frozenset({"inn", "kpp", "bik", "ogrn", "snils"})


@dataclass(frozen=True)
class LabelSet:
    labels: dict[str, str]
    queue_fields: list[str]




class LabelsInvalid(ValueError):
    """Набор подписей не прошёл проверку: errors — пары (loc, сообщение)."""

    def __init__(self, errors: list[tuple[list[str], str]]) -> None:
        super().__init__("; ".join(msg for _, msg in errors))
        self.errors = errors


def _default_title(spec) -> str:
    return spec.title or spec.name


def label_keys(schema: DocSchema) -> list[str]:
    """Ключи, которым можно дать подпись: поля верхнего уровня (и таблицы), затем «таблица.колонка»."""
    keys = [f.name for f in schema.fields]
    for f in schema.fields:
        keys.extend(f"{f.name}.{c.name}" for c in f.columns or [])
    return keys


def apply_labels(schema: DocSchema, labels: Mapping[str, str] | None) -> DocSchema:
    """Копия схемы с названиями компании в `title`; чужие ключи и пустые значения игнорируются."""
    out = schema.model_copy(deep=True)
    if not labels:
        return out

    def pick(key: str) -> str | None:
        value = labels.get(key)
        return value.strip() if isinstance(value, str) and value.strip() else None

    for f in out.fields:
        f.title = pick(f.name) or f.title
        for c in f.columns or []:
            c.title = pick(f"{f.name}.{c.name}") or c.title
    return out


def _queueable(schema: DocSchema) -> list[FieldSpec]:
    return [f for f in schema.fields if f.type != "table"]


def _label_error(label: str) -> str | None:
    if len(label) > LABEL_MAX:
        return f"не длиннее {LABEL_MAX} символов"
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in label):
        return "без управляющих символов"
    if not normalize(label):
        return "нужны буквы или цифры"
    if normalize(label) in _SERVICE_RESERVED:
        return "так называется служебная колонка таблиц"
    return None


def _duplicate_errors(
    entries: list[tuple[str, str, bool]], bad: set[str], errors: list[tuple[list[str], str]]
) -> None:
    """entries: (ключ, действующее название, это подпись компании). Пара из двух штатных названий не в счёт."""
    seen: dict[str, tuple[str, str, bool]] = {}
    for key, name, is_label in entries:
        norm = normalize(name)
        other = seen.get(norm)
        if other is None:
            seen[norm] = (key, name, is_label)
            continue
        o_key, o_name, o_label = other
        if not (is_label or o_label):
            continue
        # ошибку получает подпись компании (при двух подписях — вторая)
        key_bad, other_name = (key, o_name) if is_label else (o_key, name)
        if key_bad not in bad:
            bad.add(key_bad)
            errors.append((["labels", key_bad], f"такое название уже у поля «{other_name}»"))


def check_label_set(schema: DocSchema, labels: dict[str, str], queue_fields: list[str]) -> LabelSet:
    """Чистит (strip, пустые убираются) и проверяет набор против схемы; все ошибки — одной `LabelsInvalid`."""
    known = set(label_keys(schema))
    errors: list[tuple[list[str], str]] = []
    bad: set[str] = set()
    clean: dict[str, str] = {}
    for key, raw in labels.items():
        value = raw.strip()
        if not value:
            continue
        msg = "нет такого поля в схеме" if key not in known else _label_error(value)
        if msg:
            errors.append((["labels", key], msg))
            bad.add(key)
        else:
            clean[key] = value

    # дубли названий: поля верхнего уровня (не таблицы) и колонки каждой таблицы — внутри своей группы
    def entries(prefix: str, specs) -> list[tuple[str, str, bool]]:
        out = []
        for spec in specs:
            key = f"{prefix}{spec.name}"
            custom = clean.get(key)
            out.append((key, custom or _default_title(spec), custom is not None))
        return out

    _duplicate_errors(entries("", _queueable(schema)), bad, errors)
    for f in schema.fields:
        if f.columns:
            _duplicate_errors(entries(f"{f.name}.", f.columns), bad, errors)
    for key in bad:
        clean.pop(key, None)

    top = {f.name for f in _queueable(schema)}
    queue: list[str] = []
    for key in queue_fields:
        if key not in top:
            errors.append((["queue_fields"], f"в очередь можно только поле верхнего уровня: «{key}»"))
        elif key not in queue:
            queue.append(key)
    if len(queue) > QUEUE_MAX:
        errors.append((["queue_fields"], f"в очереди не больше {QUEUE_MAX} полей"))
    if errors:
        raise LabelsInvalid(errors)
    return LabelSet(clean, queue)


def total_field(schema: DocSchema) -> str | None:
    """Поле «итого»: total правила total_consistent, иначе другого правила с параметром total, иначе число total."""
    by_name = {f.name: f for f in schema.fields}
    for rule in sorted(schema.rules, key=lambda r: r.kind != "total_consistent"):
        name = rule.params.get("total")
        if isinstance(name, str) and name in by_name:
            return name
    spec = by_name.get("total")
    return spec.name if spec and spec.type in ("number", "integer") else None


def _account_params(schema: DocSchema) -> set[str]:
    return {
        str(r.params["account"]) for r in schema.rules if r.kind == "account_matches_bik" and r.params.get("account")
    }


def counterparty_field(schema: DocSchema) -> str | None:
    """Первое строковое поле без правил поля — название стороны (поставщик, ФИО); не номер и не счёт."""
    skip = _account_params(schema)
    for f in schema.fields:
        if f.type == "string" and not f.rules and f.name != "number" and f.name not in skip:
            return f.name
    return None


def default_queue_fields(schema: DocSchema) -> list[str]:
    """Умолчание очереди: сторона, итог, первая дата (кроме даты документа). Реквизитов с правилами нет."""
    skip = _account_params(schema)
    out: list[str] = []

    def add(name: str | None) -> None:
        spec = next((f for f in schema.fields if f.name == name), None)
        if spec is None or name in out or name in skip or any(r.kind in _ID_RULES for r in spec.rules):
            return
        out.append(spec.name)

    add(counterparty_field(schema))
    add(total_field(schema))
    add(next((f.name for f in schema.fields if f.type == "date" and f.name != "issued"), None))
    return out


def queue_fields_for(schema: DocSchema, stored: list[str]) -> list[str]:
    """Сохранённые поля очереди, которые есть в этой версии схемы; не осталось — умолчание."""
    top = {f.name for f in _queueable(schema)}
    kept: list[str] = []
    for key in stored:
        if key in top and key not in kept:
            kept.append(key)
    return kept[:QUEUE_MAX] or default_queue_fields(schema)


async def get_label_sets(session: AsyncSession, tenant_id: uuid.UUID, slugs: Iterable[str]) -> dict[str, LabelSet]:
    """Наборы подписей по slug'ам одним запросом; для slug без набора записи нет."""
    wanted = set(slugs)
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(FieldLabels).where(FieldLabels.tenant_id == tenant_id, FieldLabels.schema_slug.in_(wanted))
        )
    ).scalars()
    return {r.schema_slug: LabelSet(dict(r.labels or {}), list(r.queue_fields or [])) for r in rows}


async def get_label_set(session: AsyncSession, tenant_id: uuid.UUID, slug: str) -> LabelSet:
    # нет записи — новый пустой набор: словарь и список изменяемы, общий экземпляр протёк бы между вызовами
    return (await get_label_sets(session, tenant_id, [slug])).get(slug) or LabelSet({}, [])


async def resolve_labeled(
    resolver: "SchemaResolver", session: AsyncSession, tenant_id: uuid.UUID, slug: str, version: int | None
) -> DocSchema:
    """Схема нужной версии с названиями компании. `LookupError` (нет схемы) пробрасывается."""
    schema = await resolver.resolve(session, tenant_id, slug, version)
    return apply_labels(schema, (await get_label_set(session, tenant_id, slug)).labels)
