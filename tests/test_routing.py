"""Правила маршрутизации: условия по нормализованным данным, объединение действий."""

import uuid

import pytest

from docflow.routing import RoutingDecision, evaluate_rules, parse_rules, rule_dictionary_names
from docflow.routing_model import Rule
from docflow.schemas.model import DocSchema
from docflow.validation.dictionaries import DictEntry

SCHEMA = DocSchema.model_validate(
    {
        "slug": "invoice_ru",
        "version": 1,
        "title": "Счёт",
        "description": "т",
        "fields": [
            {"name": "number", "type": "string"},
            {"name": "issued", "type": "date"},
            {"name": "supplier", "type": "string"},
            {"name": "total", "type": "number"},
            {"name": "qty", "type": "integer"},
            {"name": "paid", "type": "boolean"},
            {"name": "note", "type": "string"},
            {"name": "inn", "type": "string"},
            {
                "name": "items",
                "type": "table",
                "columns": [{"name": "name", "type": "string"}, {"name": "amount", "type": "number"}],
            },
        ],
    }
)

DATA = {
    "number": "15",
    "issued": "2026-10-20",
    "supplier": "ООО «Ромашка»",
    "total": 150000.0,
    "qty": 3,
    "paid": False,
    "note": None,
    "inn": "7701234567",
    "items": [{"name": "Цемент М500", "amount": 150000.0}],
}

A, B, C = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def rule(*conditions, schema=None, export_to=(), notify=(), approval=False, name="правило"):
    return Rule.model_validate(
        {
            "name": name,
            "schema": schema,
            "conditions": [{"field": f, "op": op, "value": v} for f, op, v in conditions],
            "export_to": [str(i) for i in export_to],
            "notify": [str(i) for i in notify],
            "require_approval": approval,
        }
    )


def matches(*conditions, data=DATA, dictionaries=None) -> bool:
    d = evaluate_rules([rule(*conditions, export_to=[A])], SCHEMA, data, dictionaries or {})
    return d.export_to == {A}


@pytest.mark.parametrize(
    ("cond", "expected"),
    [
        (("total", "eq", 150000), True),
        (("total", "eq", "150 000,00"), True),  # число из формы кабинета строкой
        (("total", "ne", 150000), False),
        (("total", "gt", 100000), True),
        (("total", "gt", "99999.5"), True),
        (("total", "gt", 150000), False),
        (("total", "ge", 150000), True),
        (("total", "lt", 150000), False),
        (("total", "le", 150000), True),
        # числа сравниваются как числа, а не строки: "9" > "150000" лексикографически
        (("total", "lt", 9), False),
        (("qty", "ge", 3), True),
        (("qty", "lt", "10"), True),
        (("issued", "gt", "2026-10-01"), True),
        (("issued", "lt", "2026-10-01"), False),
        (("issued", "eq", "20.10.2026"), True),  # дата в привычном виде
        (("issued", "le", "2026-10-20"), True),
        (("supplier", "eq", "ооо «ромашка»"), True),  # строки — без учёта регистра
        (("supplier", "ne", "ООО «Лютик»"), True),
        (("supplier", "contains", "ромаш"), True),
        (("supplier", "contains", "лютик"), False),
        (("items", "contains", "цемент"), True),  # contains ищет и в ячейках таблицы
        (("note", "empty", None), True),
        (("note", "not_empty", None), False),
        (("supplier", "empty", None), False),
        (("supplier", "not_empty", None), True),
        (("items", "not_empty", None), True),
        (("missing", "empty", None), True),
        (("paid", "eq", "false"), True),
        (("paid", "eq", "да"), False),
        # неразборчивое значение условия — не совпадает, а не падает
        (("total", "gt", "много"), False),
        (("issued", "gt", "вчера"), False),
        # сравнение строкового поля «больше/меньше» смысла не имеет
        (("supplier", "gt", "А"), False),
        # пустое значение в документе: ничему не равно, ничего не больше
        (("note", "eq", "x"), False),
        (("note", "ne", "x"), True),
        (("note", "gt", 1), False),
    ],
)
def test_operators(cond, expected):
    assert matches(cond) is expected


def test_number_field_with_garbage_value_does_not_match():
    data = {**DATA, "total": "не число"}
    assert matches(("total", "gt", 1), data=data) is False
    assert matches(("total", "ne", 1), data=data) is True


def test_in_dictionary():
    dicts = {"Ключевые": [DictEntry("ООО «Ромашка»", ("Ромашка",))], "Пустой": []}
    assert matches(("supplier", "in_dictionary", "Ключевые"), dictionaries=dicts) is True
    assert matches(("supplier", "in_dictionary", "Пустой"), dictionaries=dicts) is False
    # справочника нет — не совпадает
    assert matches(("supplier", "in_dictionary", "Нет такого"), dictionaries=dicts) is False
    # пустое значение — не в справочнике
    assert matches(("note", "in_dictionary", "Ключевые"), dictionaries=dicts) is False
    # близкое по написанию не считается: маршрутизация по нечёткому совпадению опасна
    assert matches(("supplier", "in_dictionary", "Ключевые"), data={**DATA, "supplier": "Ромашко"},
                   dictionaries=dicts) is False


def test_all_conditions_must_hold():
    assert matches(("total", "gt", 1000), ("supplier", "contains", "ромаш")) is True
    assert matches(("total", "gt", 1000), ("supplier", "contains", "лютик")) is False


def test_rule_without_conditions_matches_everything():
    assert matches() is True


def test_schema_filter():
    rules = [rule(schema="upd_ru", export_to=[A]), rule(schema="invoice_ru", export_to=[B])]
    assert evaluate_rules(rules, SCHEMA, DATA, {}).export_to == {B}


def test_all_matching_rules_are_united():
    rules = [
        rule(("total", "gt", 100000), export_to=[A], notify=[C], name="крупные", approval=True),
        rule(export_to=[A, B], notify=[C]),
        rule(("total", "lt", 10), export_to=[C], name="мелкие", approval=True),
        rule(("total", "gt", 1), name="ещё согласование", approval=True),
    ]
    d = evaluate_rules(rules, SCHEMA, DATA, {})
    assert d == RoutingDecision(export_to={A, B}, notify={C}, approval=["крупные", "ещё согласование"])


def test_no_rules_nothing():
    assert evaluate_rules([], SCHEMA, DATA, {}) == RoutingDecision(set(), set(), [])


def test_parse_rules_tolerates_garbage():
    good = rule(export_to=[A]).model_dump(mode="json", by_alias=True)
    parsed = parse_rules([good, {"name": "битое", "conditions": [{"field": "x", "op": "??"}]}, "мусор"])
    assert len(parsed) == 1 and parsed[0].export_to == [A]
    assert parse_rules(None) == []


def test_rule_dictionary_names():
    rules = [rule(("supplier", "in_dictionary", "Ключевые"), ("total", "gt", 1)), rule(("x", "in_dictionary", "Б"))]
    assert rule_dictionary_names(rules) == {"Ключевые", "Б"}


def test_integer_value_stays_integer_and_matches_string_field():
    """JSON 7701234567 не должен стать 7701234567.0: иначе eq по ИНН/номеру не срабатывает никогда."""
    r = rule(("inn", "eq", 7701234567))
    assert r.conditions[0].value == 7701234567 and isinstance(r.conditions[0].value, int)
    assert matches(("inn", "eq", 7701234567)) is True
    assert matches(("inn", "ne", 7701234567)) is False
    assert matches(("inn", "ne", 7701234568)) is True
    # уже сохранённое старым кодом число с плавающей точкой тоже совпадает
    assert matches(("inn", "eq", 7701234567.0)) is True
    assert matches(("number", "eq", 15)) is True


def test_bool_field_with_numbers():
    assert matches(("paid", "eq", 0)) is True
    assert matches(("paid", "eq", 1)) is False
    assert matches(("paid", "ne", 1.0)) is True
