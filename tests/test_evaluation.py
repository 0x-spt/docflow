import io
import json
import logging

import pytest
from PIL import Image

from docflow.evaluation import compare, run_eval
from docflow.extraction.base import Extraction, ExtractionError


def test_compare_only_scores_expected_fields():
    s = compare({"number": "15"}, {"number": "15", "supplier": "лишнее"})
    assert s.items == {"number": True}
    assert s.all_correct


def test_compare_strings_casefold_spaces_yo():
    s = compare({"a": "Ёлкин  Иван"}, {"a": " елкин иван "})
    assert s.items == {"a": True}
    assert compare({"a": "x"}, {"a": "y"}).items == {"a": False}


def test_compare_numbers_tolerance():
    assert compare({"t": 100.0}, {"t": 100.009}).items == {"t": True}
    assert compare({"t": 100.0}, {"t": 100.02}).items == {"t": False}
    assert compare({"t": 100}, {"t": "100"}).items == {"t": False}


def test_compare_dates_as_iso_strings():
    assert compare({"d": "2026-03-05"}, {"d": "2026-03-05"}).items == {"d": True}
    assert compare({"d": "2026-03-05"}, {"d": "2026-05-03"}).items == {"d": False}


def test_compare_none_and_missing():
    assert compare({"a": None}, {"a": None}).items == {"a": True}
    assert compare({"a": None}, {"a": "x"}).items == {"a": False}
    assert compare({"a": "x"}, {"a": None}).items == {"a": False}
    assert compare({"a": "x"}, {}).items == {"a": False}


def test_compare_table_cells_by_position():
    exp = {"items": [{"name": "Болт", "qty": 2}, {"name": "Гайка", "qty": 1}]}
    act = {"items": [{"name": "болт", "qty": 2}, {"name": "Гайка", "qty": 5}]}
    assert compare(exp, act).items == {
        "items[0].name": True, "items[0].qty": True, "items[1].name": True, "items[1].qty": False,
    }


def test_compare_table_extra_and_missing_rows_are_wrong():
    exp = {"items": [{"name": "A"}]}
    extra = compare(exp, {"items": [{"name": "A"}, {"name": "B"}]})
    assert extra.items == {"items[0].name": True, "items[1]": False}
    missing = compare({"items": [{"name": "A"}, {"name": "B"}]}, {"items": [{"name": "A"}]})
    assert missing.items == {"items[0].name": True, "items[1].name": False}
    assert compare({"items": []}, {"items": []}).items == {"items": True}


# --- run_eval ---

EXPECTED = {"number": "15", "total": 100.0, "supplier_inn": "7707083893"}


def _jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, format="JPEG")
    return buf.getvalue()


def _case(root, name, expected=EXPECTED, pages=("1.jpg",)):
    d = root / name
    d.mkdir(parents=True)
    for p in pages:
        (d / p).write_bytes(_jpeg())
    if expected is not None:
        (d / "expected.json").write_text(json.dumps(expected, ensure_ascii=False), encoding="utf-8")
    return d


class Scripted:
    """Отвечает по очереди: либо словарь, либо исключение."""

    model = "claude-opus-5-5"

    def __init__(self, *answers):
        self.answers = list(answers)
        self.seen: list[int] = []

    def extract(self, schema, images):
        self.seen.append(len(images))
        a = self.answers.pop(0)
        if isinstance(a, Exception):
            raise a
        return Extraction(raw=a, model=self.model, input_tokens=1000, output_tokens=200)


_ROW = {"name": "Болт", "qty": 1, "unit": "шт", "price": 100, "amount": 100}
GOOD = {"number": "15", "issued": "2026-01-10", "total": 100, "subtotal": 100, "supplier_inn": "7707083893",
        "items": [_ROW]}
BAD = {**GOOD, "number": "16"}


def test_run_eval_metrics(tmp_path):
    root = tmp_path / "invoice_ru"
    _case(root, "a")
    _case(root, "b")
    report = run_eval(root, Scripted(GOOD, BAD))
    assert report.cases_total == 2
    by = {c.name: c for c in report.cases}
    assert by["a"].status == "ok" and by["a"].all_correct
    assert by["b"].status == "ok" and not by["b"].all_correct  # ложный ok
    assert report.ok_rate == 1.0
    assert report.ok_precision == 0.5
    assert report.overall_accuracy == pytest.approx(5 / 6)
    assert report.per_field[0][0] == "number"  # худшее поле первым
    assert report.input_tokens == 2000 and report.output_tokens == 400
    # 2000 * 4 / 1e6 + 400 * 20 / 1e6
    assert report.cost_usd == pytest.approx(0.016)
    d = report.to_dict()
    assert d["ok_precision"] == 0.5 and d["slug"] == "invoice_ru"


def test_run_eval_extraction_error_is_counted_not_fatal(tmp_path):
    root = tmp_path / "invoice_ru"
    _case(root, "a")
    _case(root, "b")
    report = run_eval(root, Scripted(ExtractionError("refusal"), GOOD))
    by = {c.name: c for c in report.cases}
    assert by["a"].status == "failed" and by["a"].error == "refusal"
    assert not by["a"].all_correct
    assert by["b"].all_correct
    assert report.failed == 1
    assert report.ok_precision == 1.0
    assert report.overall_accuracy == pytest.approx(0.5)


def test_run_eval_skips_case_without_expected_and_sorts_pages(tmp_path, caplog):
    root = tmp_path / "invoice_ru"
    _case(root, "a", pages=("10.jpg", "2.jpg", "1.jpg"))
    _case(root, "noexp", expected=None)
    ex = Scripted(GOOD)
    with caplog.at_level(logging.WARNING):
        report = run_eval(root, ex)
    assert report.cases_total == 1 and report.skipped == ["noexp"]
    assert ex.seen == [3]
    assert "noexp" in caplog.text


def test_run_eval_unknown_slug(tmp_path):
    root = tmp_path / "nope"
    root.mkdir()
    with pytest.raises(ValueError, match="nope"):
        run_eval(root, Scripted())
