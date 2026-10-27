import pytest

from docflow.extraction.normalize import normalize
from docflow.schemas.model import DocSchema


def _schema() -> DocSchema:
    return DocSchema.model_validate(
        {
            "slug": "t_doc",
            "title": "Т",
            "description": "д",
            "fields": [
                {"name": "total", "type": "number"},
                {"name": "count", "type": "integer"},
                {"name": "day", "type": "date"},
                {"name": "kind", "type": "enum", "values": ["a", "b"]},
                {"name": "note", "type": "string"},
                {"name": "paid", "type": "boolean"},
                {
                    "name": "items",
                    "type": "table",
                    "columns": [{"name": "qty", "type": "integer"}, {"name": "title", "type": "string"}],
                },
            ],
        }
    )


def _one(field: str, value):
    data, issues = normalize(_schema(), {field: value})
    return data[field], issues


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("12 500,00", 12500.0),
        ("12 500,50", 12500.5),
        ("12 500", 12500.0),
        ("12,500.00", 12500.0),
        ("1.234,5", 1234.5),
        (12, 12.0),
        ("7.5", 7.5),
    ],
)
def test_number_ok(raw, expected):
    value, issues = _one("total", raw)
    assert value == expected and isinstance(value, float) and issues == []


@pytest.mark.parametrize("raw", ["abc", True, "1,2,3", "nan", "12.500", "1.234.567", "1,5.00", "1.2.3,4"])
def test_number_bad(raw):
    value, issues = _one("total", raw)
    assert value is None
    assert [i.code for i in issues] == ["bad_value"] and issues[0].field == "total"


def test_integer():
    assert _one("count", "12 500") == (12500, [])
    value, issues = _one("count", "1 234,5")
    assert value is None and issues[0].code == "bad_value" and "1 234,5" in issues[0].message
    assert _one("count", 3.0) == (3, [])


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("01.02.2026", "2026-02-01"),
        ("2026-02-01", "2026-02-01"),
        ("01.02.26", "2026-02-01"),
        ("01.02.99", "1999-02-01"),
    ],
)
def test_date_ok(raw, expected):
    assert _one("day", raw) == (expected, [])


@pytest.mark.parametrize("raw", ["32.13.2026", "30.02.2026", "вчера"])
def test_date_bad(raw):
    value, issues = _one("day", raw)
    assert value is None and issues[0].code == "bad_value"


def test_enum_and_bool_and_string():
    assert _one("kind", "a") == ("a", [])
    value, issues = _one("kind", "z")
    assert value is None and issues[0].code == "bad_value"
    assert _one("paid", "Да") == (True, [])
    assert _one("paid", "нет") == (False, [])
    assert _one("paid", "может")[0] is None
    assert _one("note", "  hi ") == ("hi", [])
    assert _one("note", "   ") == (None, [])
    assert _one("note", None) == (None, [])
    assert _one("total", "") == (None, [])


def test_table_cellwise():
    data, issues = normalize(
        _schema(),
        {"items": [{"qty": "2", "title": " x "}, {"qty": "1,5", "title": "y"}]},
    )
    assert data["items"] == [{"qty": 2, "title": "x"}, {"qty": None, "title": "y"}]
    assert [(i.field, i.code) for i in issues] == [("items[1].qty", "bad_value")]


def test_table_none_is_empty():
    assert _one("items", None) == ([], [])


def test_extra_keys_dropped_and_unknown_uncertain_reported():
    data, issues = normalize(_schema(), {"total": "1", "ghost": 5, "_uncertain": ["total", "ghost"]})
    assert "ghost" not in data
    assert data["_uncertain"] == ["total"]
    assert data["note"] is None
    # неизвестное имя не теряется молча: документ всё равно уйдёт на проверку
    assert [(i.field, i.code) for i in issues] == [(None, "uncertain")]
    assert "«ghost»" in issues[0].message
    assert normalize(_schema(), {})[0]["_uncertain"] == []
    assert normalize(_schema(), {})[1] == []


def test_uncertain_not_a_list_fails_closed():
    for bad in ("total", {"total": True}, 5, True):
        data, issues = normalize(_schema(), {"total": "1", "_uncertain": bad})
        assert data["_uncertain"] == []
        assert [(i.field, i.code) for i in issues] == [("_uncertain", "uncertain")]


def test_uncertain_nested_path_maps_to_top_level_field():
    raw = {"_uncertain": ["items[0].qty", "items[1].title", "items.qty", "day", "day"]}
    data, issues = normalize(_schema(), raw)
    assert data["_uncertain"] == ["items", "day"]
    assert issues == []


def test_uncertain_non_string_entries_reported():
    data, issues = normalize(_schema(), {"_uncertain": ["total", 7, None, "itemz[0].qty"]})
    assert data["_uncertain"] == ["total"]
    assert [(i.field, i.code) for i in issues] == [(None, "uncertain")]
    assert "«7»" in issues[0].message and "«None»" in issues[0].message and "«itemz[0].qty»" in issues[0].message


def test_dot_thousands_integer_is_bad():
    value, issues = _one("count", "12.000")
    assert value is None and issues[0].code == "bad_value"


def test_plain_dot_decimal_ok():
    assert _one("total", "7.5") == (7.5, [])
    assert _one("total", "12.50") == (12.5, [])
    assert _one("total", "1.234,50") == (1234.5, [])
    assert _one("total", "1,234.50") == (1234.5, [])
