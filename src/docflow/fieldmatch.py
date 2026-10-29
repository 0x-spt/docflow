"""Сопоставление названий полей: нормализация текста и автоподбор полей системы клиента к полям документа."""

import difflib
import re
from collections.abc import Set as AbstractSet
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # labels и коннекторы сами импортируют этот модуль: только для типов
    from docflow.connectors.base import RemoteField
    from docflow.schemas.model import DocSchema, FieldSpec

_SPACES = re.compile(r"\s+")
_EDGE = ".,;:!?()[]{}«»\"'*-–—/\\ "

# группы синонимов: название поля системы → смысл поля документа (уже в виде normalize)
SYNONYMS: dict[str, tuple[str, ...]] = {
    "inn": ("инн",),
    "kpp": ("кпп",),
    "bik": ("бик",),
    "total": ("сумма", "итого", "итог", "к оплате", "всего к оплате", "сумма к оплате", "сумма сделки"),
    "vat": ("ндс", "сумма ндс", "в т ч ндс"),
    "date": ("дата", "дата документа"),
    "number": ("номер", "номер документа", "№"),
    "party": ("поставщик", "продавец", "контрагент"),
}
SIMILARITY = 0.8

# слово сразу после синонима, меняющее смысл: «Сумма без НДС» — не итог к оплате
_QUALIFIERS = frozenset({"без", "до"})
# синонимы от длинных к коротким: «сумма ндс» проверяется раньше «сумма»
_BY_LENGTH = sorted(((s, g) for g, syns in SYNONYMS.items() for s in syns), key=lambda p: -len(p[0]))


def normalize(text: str) -> str:
    """Для сравнения названий: без регистра и «ё», один пробел, без знаков по краям (внутри знаки остаются)."""
    text = text.casefold().replace("ё", "е")
    return _SPACES.sub(" ", text).strip(_EDGE)


def _variants(spec: "FieldSpec", base: "FieldSpec | None") -> set[str]:
    """Как поле документа может называться у клиента: подпись компании, наше название, служебное имя."""
    raw = [spec.title or "", spec.name, spec.name.replace("_", " ")]
    if base is not None:
        raw.append(base.title or base.name)
    return {v for v in (normalize(r) for r in raw) if v}


def is_own_name(code: str, schema: "DocSchema", base: "DocSchema", key: str) -> bool:
    """Код совпадает с одним из наших названий поля `key` (подпись, название по умолчанию, имя)."""
    spec = next((f for f in schema.fields if f.name == key), None)
    if spec is None:
        return False
    base_spec = next((f for f in base.fields if f.name == key), None)
    return normalize(code) in _variants(spec, base_spec)


def _synonym_group(title: str) -> str | None:
    for syn, group in _BY_LENGTH:
        if title == syn:
            return group
        if title.startswith(syn + " "):
            rest = title[len(syn) + 1 :].split(" ", 1)[0]
            return None if rest in _QUALIFIERS else group
    return None


def _group_fields(group: str, schema: "DocSchema") -> list[str]:
    """Поля документа группы синонимов в порядке предпочтения."""
    from docflow.labels import counterparty_field, total_field  # labels импортирует этот модуль

    fields = schema.fields
    if group in ("inn", "kpp", "bik"):
        return [f.name for f in fields if any(r.kind == group for r in f.rules)]
    if group == "total":
        return [n for n in [total_field(schema)] if n]
    if group == "vat":
        return [f.name for f in fields if f.name == "vat"]
    if group == "date":
        issued = [f.name for f in fields if f.name == "issued"]
        return issued or [f.name for f in fields if f.type == "date"][:1]
    if group == "number":
        return [f.name for f in fields if f.name == "number"]
    if group == "party":
        return [n for n in [counterparty_field(schema), "supplier", "buyer"] if n]
    return []


def suggest(
    schema: "DocSchema",
    base: "DocSchema",
    fields: "list[RemoteField]",
    *,
    skip_keys: AbstractSet[str] = frozenset(),
    taken_codes: AbstractSet[str] = frozenset(),
) -> dict[str, str]:
    """Предложение сопоставления: ключ поля документа → code поля системы.

    schema — с подписями компании, base — штатная. Кандидаты — нетабличные поля верхнего уровня, кроме skip_keys;
    поля системы с code из taken_codes не предлагаются. Каждое поле с обеих сторон — не больше одного раза.
    Проходы: точное совпадение названия → синонимы → похожесть (difflib, ≥ SIMILARITY).
    """
    base_by_name = {f.name: f for f in base.fields}
    order = [f.name for f in schema.fields if f.type != "table" and f.name not in skip_keys]
    variants = {f.name: _variants(f, base_by_name.get(f.name)) for f in schema.fields if f.name in order}
    remote = [rf for rf in fields if rf.code not in taken_codes]
    out: dict[str, str] = {}

    def free(name: str) -> bool:
        return name in variants and name not in out

    def open_(code: str) -> bool:
        # code уже предложен (в т. ч. повтор кода в списке системы) — второй раз не отдаём
        return code not in out.values()

    # 1. точное совпадение названия или кода с любым вариантом названия поля документа
    for rf in remote:
        keys = {k for k in (normalize(rf.title), normalize(rf.code)) if k}
        name = next((n for n in order if free(n) and variants[n] & keys), None)
        if name and open_(rf.code):
            out[name] = rf.code

    # 2. синонимы: «ИНН контрагента» → первое свободное поле с правилом inn
    for rf in remote:
        group = _synonym_group(normalize(rf.title))
        if not group or not open_(rf.code):
            continue
        name = next((n for n in _group_fields(group, schema) if free(n)), None)
        if name:
            out[name] = rf.code

    # 3. похожесть: жадно по убыванию ratio, при равенстве — порядок схемы
    pairs: list[tuple[float, int, int, str]] = []
    for i, rf in enumerate(remote):
        title = normalize(rf.title)
        if not title or not open_(rf.code):
            continue
        for pos, name in enumerate(order):
            if not free(name):
                continue
            ratio = max((difflib.SequenceMatcher(None, title, v).ratio() for v in variants[name]), default=0.0)
            if ratio >= SIMILARITY:
                pairs.append((-ratio, pos, i, name))
    for _, _, i, name in sorted(pairs):
        if free(name) and open_(remote[i].code):
            out[name] = remote[i].code
    return out
