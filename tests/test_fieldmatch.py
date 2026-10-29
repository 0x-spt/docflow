"""Автоподбор полей системы к полям документа: точное совпадение, синонимы, похожесть, одно к одному."""

from docflow.connectors.base import RemoteField as RF
from docflow.fieldmatch import SIMILARITY, SYNONYMS, suggest
from docflow.labels import apply_labels
from docflow.schemas.library import builtin_schemas

INV = builtin_schemas()["invoice_ru"]


def test_constants():
    assert SIMILARITY == 0.8
    assert SYNONYMS["vat"] == ("ндс", "сумма ндс", "в т ч ндс")
    assert "к оплате" in SYNONYMS["total"]


def test_exact_by_label_default_and_name():
    schema = apply_labels(INV, {"vat": "Налог"})
    fields = [RF("1", "Налог"), RF("2", "Поставщик"), RF("3", "supplier_inn")]
    assert suggest(schema, INV, fields) == {"vat": "1", "supplier": "2", "supplier_inn": "3"}


def test_exact_by_default_title_when_label_set():
    # штатное название подходит и при своей подписи компании
    schema = apply_labels(INV, {"vat": "Налог"})
    assert suggest(schema, INV, [RF("x", "ндс")]) == {"vat": "x"}


def test_synonyms():
    fields = [RF("10", "ИНН контрагента"), RF("11", "Сумма НДС"), RF("12", "К оплате")]
    out = suggest(INV, INV, fields)
    assert out["supplier_inn"] == "10"
    assert out["vat"] == "11"
    assert out["total"] == "12"


def test_synonym_party_and_date():
    out = suggest(INV, INV, [RF("p", "Контрагент"), RF("d", "Дата документа"), RF("n", "№")])
    assert out == {"supplier": "p", "issued": "d", "number": "n"}


def test_similarity_threshold():
    assert suggest(INV, INV, [RF("a", "Поставщикк")]) == {"supplier": "a"}
    assert suggest(INV, INV, [RF("b", "Пост")]) == {}


def test_one_to_one_and_skips():
    # одно поле документа — одному полю системы: второе «ИНН» уходит в следующее поле с правилом inn,
    # третьему свободного места нет
    out = suggest(INV, INV, [RF("a", "ИНН"), RF("b", "ИНН"), RF("c", "ИНН")])
    assert out == {"supplier_inn": "a", "buyer_inn": "b"}
    # повтор кода в списке системы не раздаётся двум полям
    assert suggest(INV, INV, [RF("a", "ИНН"), RF("a", "ИНН")]) == {"supplier_inn": "a"}

    fields = [RF("1", "НДС"), RF("2", "Сумма НДС"), RF("3", "Поставщик")]
    out = suggest(INV, INV, fields, skip_keys={"vat"}, taken_codes={"1"})
    assert "vat" not in out
    assert "1" not in out.values()
    assert out == {"supplier": "3"}


def test_tables_not_candidates():
    assert suggest(INV, INV, [RF("i", "Позиции"), RF("j", "items")]) == {}
