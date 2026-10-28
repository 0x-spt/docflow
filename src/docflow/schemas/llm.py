"""JSON Schema, которую обязана заполнить vision-модель, из DocSchema."""
from typing import Any

from docflow.schemas.model import ColumnSpec, DocSchema

UNCERTAIN = "_uncertain"

# Дату переписываем символ в символ, разбор и год — в normalize: слабые модели превращают «24.10» в «2410-10-24»
_DATE_HINT = (
    "Дату перепиши как написана на документе, не меняя формат и не дописывая год: «24.10», «22 окт. 26», "
    "«23.10.2026»."
)
_UNCERTAIN_DESC = (
    "Имена полей этой схемы, которые на документе есть, но прочитать их уверенно нельзя "
    "(размыто, перекрыто, неразборчиво). Не угадывай — назови поле здесь и верни для него null. "
    "Пустой список, если всё прочитано уверенно."
)


def _scalar(spec: ColumnSpec) -> dict[str, Any]:
    desc = spec.description
    if spec.type == "enum":
        node: dict[str, Any] = {"type": ["string", "null"], "enum": [*(spec.values or []), None]}
    elif spec.type == "date":
        node = {"type": ["string", "null"]}
        desc = f"{desc} {_DATE_HINT}".strip()
    else:
        node = {"type": [spec.type, "null"]}
    node["description"] = desc
    return node


def to_json_schema(schema: DocSchema) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for f in schema.fields:
        if f.type == "table":
            cols = f.columns or []
            props[f.name] = {
                "type": "array",
                "description": f"{f.description} Пустой массив, если строк нет.".strip(),
                "items": {
                    "type": "object",
                    "properties": {c.name: _scalar(c) for c in cols},
                    "required": [c.name for c in cols],
                    "additionalProperties": False,
                },
            }
        else:
            props[f.name] = _scalar(f)
    props[UNCERTAIN] = {
        "type": "array",
        "description": _UNCERTAIN_DESC,
        "items": {"type": "string", "enum": [f.name for f in schema.fields]},
    }
    return {
        "type": "object",
        "properties": props,
        "required": [*props],
        "additionalProperties": False,
    }
