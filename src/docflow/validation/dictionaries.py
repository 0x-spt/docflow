"""Справочники тенанта: значение поля сверяется со списком известных (клиники, врачи, контрагенты).

Точное совпадение (без учёта регистра, пунктуации, пробелов и ё/е) приводит запись к виду из справочника.
Близкое совпадение — исправление на единственного близкого кандидата: модель часто путает одну-две буквы
в рукописи («Дента-Локс» → «Дента-Люкс»). Исправление видно клиенту (issue `corrected`), но статус не портит.
Нет кандидата или их несколько — `not_in_dictionary`, документ уходит человеку: выдуманное моделью название
(«Drama - Love») хуже пустого поля.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from docflow.schemas.model import DocSchema
from docflow.validation.engine import Issue

# Порог похожести для автоисправления и отрыв от второго кандидата. Подобраны на рукописных нарядах:
# одна-две ошибочные буквы в фамилии с инициалами дают 0.78–0.9, другое название — ниже 0.7.
FUZZY_MIN = 0.75
FUZZY_MARGIN = 0.08
SUGGEST_MIN = 0.5
_MIN_FUZZY_LEN = 4  # короче — любое совпадение случайно


@dataclass(frozen=True)
class DictEntry:
    value: str
    aliases: Sequence[str] = field(default_factory=tuple)


@dataclass(frozen=True)
class Match:
    value: str
    exact: bool
    score: float


def dict_key(s: str) -> str:
    """Ключ сравнения: без регистра, пунктуации, пробелов; ё = е."""
    return re.sub(r"[\W_]+", "", s.lower().replace("ё", "е"))


# Латиница и кириллица в один «скелет»: модель пишет «Denta - luxe» вместо «Дента-Люкс». Схлопываем и близкие
# звуки (ю/у, ы/и, x/кс), чтобы транслитерации разных систем сходились; это только для нечёткого сравнения
_CYR = dict(zip("абвгдежзийклмнопрстуфхцчшщъыьэюя", [
    "a", "b", "v", "g", "d", "e", "zh", "z", "i", "i", "k", "l", "m", "n", "o", "p", "r", "s", "t", "u", "f",
    "h", "ts", "ch", "sh", "sh", "", "i", "", "e", "u", "a",
], strict=True))  # fmt: skip
_LAT = {"x": "ks", "y": "i", "j": "i", "w": "v", "q": "k", "c": "k"}


def _skeleton(s: str) -> str:
    return "".join(_CYR.get(ch, _LAT.get(ch, ch)) for ch in dict_key(s))


def _scored(raw: str, entries: Sequence[DictEntry]) -> list[tuple[float, str]]:
    """Лучшая похожесть скелетов для каждой записи (по значению и алиасам), по убыванию."""
    key = _skeleton(raw)
    out = []
    for e in entries:
        best = max(SequenceMatcher(None, key, _skeleton(v)).ratio() for v in (e.value, *e.aliases))
        out.append((best, e.value))
    return sorted(out, key=lambda t: -t[0])


def match(raw: str, entries: Sequence[DictEntry], *, fuzzy: bool = True) -> Match | None:
    key = dict_key(raw)
    if not key:
        return None
    for e in entries:
        if any(dict_key(v) == key for v in (e.value, *e.aliases)):
            return Match(e.value, True, 1.0)
    if not fuzzy or len(key) < _MIN_FUZZY_LEN or not entries:
        return None
    scored = _scored(raw, entries)
    best_score, best_value = scored[0]
    second = scored[1][0] if len(scored) > 1 else 0.0
    # скелет совпал полностью (другая письменность) и только у одной записи — отрыв не нужен
    if best_score == 1.0 and second < 1.0:
        return Match(best_value, False, best_score)
    if best_score >= FUZZY_MIN and best_score - second >= FUZZY_MARGIN:
        return Match(best_value, False, best_score)
    return None


def _similar(raw: str, entries: Sequence[DictEntry]) -> list[str]:
    """Записи со скелетной похожестью ≥ SUGGEST_MIN, лучшие первыми."""
    return [v for s, v in _scored(raw, entries) if s >= SUGGEST_MIN]


def nearest(raw: str, entries: Sequence[DictEntry]) -> str | None:
    """Самая похожая запись справочника или None, если ни одна не дотягивает до SUGGEST_MIN."""
    similar = _similar(raw, entries)
    return similar[0] if similar else None


def _suggestion(raw: str, entries: Sequence[DictEntry]) -> str:
    scored = _similar(raw, entries)[:3]
    return f"; похожие: {', '.join(f'«{v}»' for v in scored)}" if scored else ""


def resolve_dictionaries(
    schema: DocSchema, data: dict[str, Any], dictionaries: Mapping[str, Sequence[DictEntry]]
) -> list[Issue]:
    """Сверяет поля с правилом `dictionary` и правит data на месте (каноническое значение из справочника)."""
    issues: list[Issue] = []
    for f in schema.fields:
        for rule in f.rules:
            if rule.kind != "dictionary":
                continue
            value = data.get(f.name)
            if not isinstance(value, str) or not value.strip():
                continue
            name = rule.params["name"]
            entries = dictionaries.get(name)
            if entries is None:
                issues.append(Issue(f.name, "dictionary_missing", f"справочник «{name}» не заведён — сверить не с чем"))
                continue
            m = match(value, entries, fuzzy=bool(rule.params.get("fuzzy", True)))
            if m is None:
                issues.append(
                    Issue(
                        f.name,
                        "not_in_dictionary",
                        f"«{value}» нет в справочнике «{name}»{_suggestion(value, entries)}",
                    )
                )
                continue
            if not m.exact:
                message = f"«{value}» исправлено на «{m.value}» по справочнику «{name}»"
                issues.append(Issue(f.name, "corrected", message))
            data[f.name] = m.value
    return issues
