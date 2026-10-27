import jsonschema

from docflow.schemas.library import builtin_schemas, load_schema
from docflow.schemas.llm import to_json_schema


def test_builtins_load():
    schemas = builtin_schemas()
    assert set(schemas) == {"invoice_ru", "upd_ru", "passport_ru"}
    for slug, s in schemas.items():
        assert s.slug == slug
        jsonschema.Draft202012Validator.check_schema(to_json_schema(s))


def test_load_schema_from_path(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("slug: xx\ntitle: T\ndescription: D\nfields:\n  - {name: a, type: string}\n", encoding="utf-8")
    assert load_schema(p).fields[0].name == "a"
