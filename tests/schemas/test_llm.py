from docflow.schemas.llm import UNCERTAIN, to_json_schema
from docflow.schemas.model import ColumnSpec, DocSchema, FieldSpec


def _schema():
    return DocSchema(
        slug="doc_x",
        title="T",
        description="D",
        fields=[
            FieldSpec(name="number", type="string", description="Номер"),
            FieldSpec(name="total", type="number", description="Итог"),
            FieldSpec(name="issued", type="date", description="Дата"),
            FieldSpec(name="kind", type="enum", values=["a", "b"], description="Вид"),
            FieldSpec(
                name="items",
                type="table",
                description="Позиции",
                columns=[ColumnSpec(name="name", type="string"), ColumnSpec(name="qty", type="integer")],
            ),
        ],
    )


def test_json_schema_shape():
    js = to_json_schema(_schema())
    assert js["type"] == "object"
    assert js["required"] == ["number", "total", "issued", "kind", "items", "_uncertain"]
    assert UNCERTAIN == "_uncertain"
    assert js["additionalProperties"] is False
    p = js["properties"]
    assert p["total"]["type"] == ["number", "null"]
    assert p["issued"]["type"] == ["string", "null"]
    # дату модель переписывает как есть, разбирает её normalize: слабые модели портят перевод «24.10» в ISO
    assert "как написана" in p["issued"]["description"]
    assert p["issued"]["description"].startswith("Дата")
    assert set(p["issued"]) == {"type", "description"}
    assert p["kind"]["enum"] == ["a", "b", None]
    assert p["items"]["type"] == "array"
    item = p["items"]["items"]
    assert item["additionalProperties"] is False
    assert item["required"] == ["name", "qty"]
    assert item["properties"]["qty"]["type"] == ["integer", "null"]
    assert set(p["_uncertain"]["items"]["enum"]) == {"number", "total", "issued", "kind", "items"}
    assert p["_uncertain"]["type"] == "array"


def _unions(node) -> int:
    """Сколько параметров с union-типом (type-массив или anyOf) в JSON Schema."""
    if isinstance(node, dict):
        own = int(isinstance(node.get("type"), list) or "anyOf" in node)
        return own + sum(_unions(v) for v in node.values())
    if isinstance(node, list):
        return sum(_unions(v) for v in node)
    return 0


def test_max_unions_keeps_builtin_schemas_within_claude_limit():
    from docflow.schemas.library import builtin_schemas

    for slug, schema in builtin_schemas().items():
        assert _unions(to_json_schema(schema, max_unions=16)) <= 16, slug
    # без предела (Ollama) схема прежняя
    assert _unions(to_json_schema(builtin_schemas()["invoice_ru"])) > 16


def test_max_unions_compact_shape():
    p = to_json_schema(_schema(), max_unions=1)["properties"]
    # текстовые поля — без null, пустое значение — пустая строка
    assert p["number"]["type"] == "string" and "Пустая строка" in p["number"]["description"]
    assert p["issued"]["type"] == "string" and "как написана" in p["issued"]["description"]
    assert p["kind"] == {**p["kind"], "type": "string", "enum": ["a", "b", ""]}
    # первое число получает бюджет, следующее сверх бюджета — строкой
    assert p["total"]["type"] == ["number", "null"]
    assert p["items"]["items"]["properties"]["qty"]["type"] == "string"


def test_compact_empty_strings_normalize_to_null():
    from docflow.extraction.normalize import normalize

    raw = {"number": "", "total": None, "issued": "", "kind": "", "items": [{"name": "", "qty": "3"}],
           "_uncertain": []}
    data, issues = normalize(_schema(), raw)
    assert data["number"] is None and data["issued"] is None and data["kind"] is None
    assert data["items"][0]["qty"] == 3
    assert not [i for i in issues if i.code == "bad_value"]
