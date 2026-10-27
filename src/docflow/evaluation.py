"""Эталонный прогон: документы с известными ответами -> метрики качества по схеме.

Главная метрика — ok_precision: из документов со статусом ok доля тех, где ВСЕ поля верны.
Ложный ok (система уверена, а данные неверны) — то, чего мы не должны допускать.
"""
import json
import logging
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from docflow.extraction.base import ExtractionError, Extractor
from docflow.extraction.normalize import normalize
from docflow.imaging.enhance import Mode
from docflow.imaging.load import UnsupportedFile
from docflow.imaging.prepare import prepare_pages
from docflow.schemas.library import builtin_schemas
from docflow.schemas.llm import UNCERTAIN
from docflow.validation.engine import decide_status, evaluate

log = logging.getLogger(__name__)

PAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".pdf", ".tif", ".tiff", ".webp"}
NUMBER_TOLERANCE = 0.01

# $ за миллион токенов (вход, выход). Локальные модели бесплатны; неизвестная модель -> стоимость None.
PRICES: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.0, 20.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
}

_MISSING = object()
_SPACES = re.compile(r"\s+")


def _norm_str(s: str) -> str:
    return _SPACES.sub(" ", s.casefold().replace("ё", "е")).strip()


def _is_number(v: Any) -> bool:
    return isinstance(v, int | float) and not isinstance(v, bool)


def _equal(expected: Any, actual: Any) -> bool:
    if actual is _MISSING:
        return False
    if expected is None:
        return actual is None
    if isinstance(expected, bool):
        return isinstance(actual, bool) and expected == actual  # True не равно 1
    if _is_number(expected):
        return _is_number(actual) and abs(expected - actual) <= NUMBER_TOLERANCE
    if isinstance(expected, str):
        return isinstance(actual, str) and _norm_str(expected) == _norm_str(actual)
    return expected == actual  # bool; даты уже ISO-строки


@dataclass
class FieldScores:
    """Оценка каждого сравниваемого значения: «поле» или «таблица[i].колонка» -> верно ли."""

    items: dict[str, bool] = field(default_factory=dict)

    @property
    def all_correct(self) -> bool:
        # пустая оценка — не «всё верно»: иначе пустой эталон завышает ok_precision
        return bool(self.items) and all(self.items.values())

    @property
    def correct(self) -> int:
        return sum(self.items.values())

    @property
    def total(self) -> int:
        return len(self.items)


def compare(expected: dict, actual: dict) -> FieldScores:
    """Оцениваются только поля из expected — эталон может быть частичным."""
    scores = FieldScores()
    for name, exp in expected.items():
        act = actual.get(name, _MISSING)
        if not isinstance(exp, list):
            scores.items[name] = _equal(exp, act)
            continue
        act_rows = act if isinstance(act, list) else []
        if not exp and not act_rows:
            scores.items[name] = isinstance(act, list)
            continue
        for i in range(max(len(exp), len(act_rows))):
            if i >= len(exp):
                scores.items[f"{name}[{i}]"] = False  # лишняя строка
                continue
            arow = act_rows[i] if i < len(act_rows) and isinstance(act_rows[i], dict) else {}
            for col, cexp in exp[i].items():
                scores.items[f"{name}[{i}].{col}"] = _equal(cexp, arow.get(col, _MISSING))
    return scores


@dataclass
class CaseResult:
    name: str
    status: str  # ok | needs_review | failed
    scores: FieldScores
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def all_correct(self) -> bool:
        return self.status != "failed" and self.scores.all_correct


def _field_of(key: str) -> str:
    return re.split(r"\[", key, maxsplit=1)[0]


@dataclass
class EvalReport:
    slug: str
    model: str
    cases: list[CaseResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def cases_total(self) -> int:
        return len(self.cases)

    @property
    def failed(self) -> int:
        return sum(c.status == "failed" for c in self.cases)

    @property
    def ok_cases(self) -> list[CaseResult]:
        return [c for c in self.cases if c.status == "ok"]

    @property
    def ok_rate(self) -> float | None:
        return len(self.ok_cases) / len(self.cases) if self.cases else None

    @property
    def ok_precision(self) -> float | None:
        ok = self.ok_cases
        return sum(c.all_correct for c in ok) / len(ok) if ok else None

    @property
    def overall_accuracy(self) -> float | None:
        total = sum(c.scores.total for c in self.cases)
        return sum(c.scores.correct for c in self.cases) / total if total else None

    @property
    def per_field(self) -> list[tuple[str, float, int]]:
        """(поле, доля верных, всего значений), худшие первыми. Ячейки таблицы входят в поле-таблицу."""
        ok: dict[str, int] = defaultdict(int)
        tot: dict[str, int] = defaultdict(int)
        for c in self.cases:
            for key, good in c.scores.items.items():
                tot[_field_of(key)] += 1
                ok[_field_of(key)] += good
        rows = [(f, ok[f] / tot[f], tot[f]) for f in tot]
        return sorted(rows, key=lambda r: (r[1], r[0]))

    @property
    def input_tokens(self) -> int:
        return sum(c.input_tokens for c in self.cases)

    @property
    def output_tokens(self) -> int:
        return sum(c.output_tokens for c in self.cases)

    @property
    def cost_usd(self) -> float | None:
        price = PRICES.get(self.model)
        if price is None:
            # не-claude модели считаем локальными (ollama, fake): цена 0.
            # Неизвестная модель Claude -> цена неизвестна.
            return None if self.model.startswith("claude") else 0.0
        return (self.input_tokens * price[0] + self.output_tokens * price[1]) / 1_000_000

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "model": self.model,
            "cases_total": self.cases_total,
            "failed": self.failed,
            "skipped": self.skipped,
            "ok_precision": self.ok_precision,
            "ok_rate": self.ok_rate,
            "overall_accuracy": self.overall_accuracy,
            "per_field": [{"field": f, "accuracy": a, "values": n} for f, a, n in self.per_field],
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": self.cost_usd,
            "cases": [
                {
                    "name": c.name,
                    "status": c.status,
                    "all_correct": c.all_correct,
                    "error": c.error,
                    "wrong": [k for k, v in c.scores.items.items() if not v],
                }
                for c in self.cases
            ],
        }


def _page_key(p: Path) -> tuple[int, str]:
    return (int(p.stem), p.name) if p.stem.isdigit() else (10**9, p.name)


def _pages(case: Path) -> list[Path]:
    return sorted((p for p in case.iterdir() if p.is_file() and p.suffix.lower() in PAGE_SUFFIXES), key=_page_key)


def _run_case(case: Path, expected: dict, schema, extractor: Extractor, mode: Mode, max_pages: int) -> CaseResult:
    name = case.name
    try:
        files = [(p.read_bytes(), p.name) for p in _pages(case)]
        if not files:
            raise ExtractionError("no_pages", "в кейсе нет файлов страниц")
        images = [p.model_jpeg for p in prepare_pages(files, mode, max_pages)]
        extraction = extractor.extract(schema, images)
    except (ExtractionError, UnsupportedFile) as e:
        reason = e.reason if isinstance(e, ExtractionError) else str(e)
        log.warning("кейс %s: извлечение не удалось (%s)", name, reason)
        return CaseResult(name, "failed", compare(expected, {}), error=reason)
    data, issues = normalize(schema, extraction.raw)
    issues += evaluate(schema, data)
    data.pop(UNCERTAIN, None)
    return CaseResult(
        name,
        decide_status(issues),
        compare(expected, data),
        input_tokens=extraction.input_tokens,
        output_tokens=extraction.output_tokens,
    )


def _load_expected(case: Path) -> dict:
    path = case / "expected.json"
    try:
        expected = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        raise ValueError(f"кейс «{case.name}»: не удалось прочитать {path}: {e}") from e
    if not isinstance(expected, dict):
        raise ValueError(f"кейс «{case.name}»: {path} должен быть объектом {{поле: значение}}")
    if not expected:
        raise ValueError(f"кейс «{case.name}»: {path} пуст, оценивать нечего")
    for key, value in expected.items():
        if isinstance(value, list) and not all(isinstance(r, dict) for r in value):
            raise ValueError(f"кейс «{case.name}»: {path}, поле «{key}»: строки таблицы должны быть объектами")
    return expected


def run_eval(root: Path, extractor: Extractor, mode: Mode = "gray", max_pages: int = 30) -> EvalReport:
    """root = evals/<slug>; slug — имя папки, обязан быть встроенной схемой."""
    root = Path(root)
    schema = builtin_schemas().get(root.name)
    if schema is None:
        raise ValueError(f"«{root.name}» — не встроенная схема (доступны: {', '.join(sorted(builtin_schemas()))})")
    report = EvalReport(slug=schema.slug, model=extractor.model)
    for case in sorted(p for p in root.iterdir() if p.is_dir()):
        exp_file = case / "expected.json"
        if not exp_file.exists():
            log.warning("кейс %s пропущен: нет expected.json", case.name)
            report.skipped.append(case.name)
            continue
        expected = _load_expected(case)
        report.cases.append(_run_case(case, expected, schema, extractor, mode, max_pages))
    return report


def _pct(x: float | None) -> str:
    return "—" if x is None else f"{x:.1%}"


def format_report(r: EvalReport) -> str:
    lines = [
        f"Эталонный прогон: {r.slug}   модель: {r.model}",
        f"Документов: {r.cases_total} (сбоев извлечения: {r.failed}, пропущено без expected.json: {len(r.skipped)})",
        "",
        f"  ok_precision (ok без ошибок, главное)  {_pct(r.ok_precision)}",
        f"  ok_rate (доля документов со статусом ok)  {_pct(r.ok_rate)}",
        f"  overall_accuracy (верных значений)  {_pct(r.overall_accuracy)}",
        "",
        f"  {'Поле':<28}{'Верно':>8}{'Значений':>10}",
    ]
    lines += [f"  {f:<28}{_pct(a):>8}{n:>10}" for f, a, n in r.per_field]
    lines += ["", f"  Токены: вход {r.input_tokens}, выход {r.output_tokens}"]
    cost = r.cost_usd
    lines.append("  Стоимость: " + ("цена модели неизвестна" if cost is None else f"${cost:.4f}"))
    wrong = [c for c in r.cases if not c.all_correct]
    if wrong:
        lines += ["", "  С ошибками:"]
        for c in wrong:
            what = c.error or ", ".join(k for k, v in c.scores.items.items() if not v)
            lines.append(f"    {c.name} [{c.status}]: {what}")
    return "\n".join(lines)
