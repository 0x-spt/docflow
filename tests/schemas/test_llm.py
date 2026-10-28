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
