import subprocess
import sys


def test_tabular_imports_first_in_fresh_interpreter():
    """Раньше `import docflow.tabular` падал циклическим импортом через connectors."""
    done = subprocess.run([sys.executable, "-c", "import docflow.tabular"], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_doc_fields_follow_schema_order_not_mapping_order():
    from docflow.schemas.model import DocSchema
    from docflow.tabular import doc_fields, docs_header

    schema = DocSchema(
        slug="abc",
        title="T",
        description="d",
        fields=[
            {"name": "a", "type": "string", "title": "Альфа"},
            {"name": "b", "type": "string"},
            {"name": "c", "type": "string"},
        ],
    )
    # порядок ключей маппинга другой (JSONB его не хранит), пустая колонка - название по умолчанию
    mapping = {"c": "Колонка C", "a": "", "zzz": "нет такого поля"}
    assert [(s.name, t) for s, t in doc_fields(schema, mapping)] == [("a", "Альфа"), ("c", "Колонка C")]
    assert docs_header(schema, mapping)[4:6] == ["Альфа", "Колонка C"]
