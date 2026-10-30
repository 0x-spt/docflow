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
    "(размыто, перекрыто, неразборчиво). Не угадывай — назови поле здесь и верни для него null или пустую строку. "
    "Пустой список, если всё прочитано уверенно."
)


_EMPTY_HINT = "Пустая строка, если поля нет или его не прочитать."


class _Unions:
    """Сколько ещё параметров может быть nullable (union). None — без ограничения."""

    def __init__(self, budget: int | None):
        self.left = budget

    def take(self) -> bool:
        if self.left is None:
            return True
        if self.left <= 0:
            return False
        self.left -= 1
        return True


def _scalar(spec: ColumnSpec, unions: _Unions, compact: bool) -> dict[str, Any]:
    desc = spec.description
    if spec.type == "date":
        desc = f"{desc} {_DATE_HINT}".strip()
    # compact: строки, даты и enum — без null, «нет значения» = пустая строка (normalize читает её как null);
    # числа и булевы nullable, пока хватает бюджета, дальше — строкой (normalize разбирает и её)
    textual = spec.type in ("string", "date", "enum")
    if compact and (textual or not unions.take()):
        node: dict[str, Any] = {"type": "string"}
        if spec.type == "enum":
            node["enum"] = [*(spec.values or []), ""]
        desc = f"{desc} {_EMPTY_HINT}".strip()
    elif spec.type == "enum":
        node = {"type": ["string", "null"], "enum": [*(spec.values or []), None]}
    elif spec.type == "date":
        node = {"type": ["string", "null"]}
    else:
        node = {"type": [spec.type, "null"]}
    node["description"] = desc
    return node


def to_json_schema(schema: DocSchema, *, max_unions: int | None = None) -> dict[str, Any]:
    """max_unions — предел nullable-параметров (у structured outputs Claude их не больше 16); с ним
    схема компактная: текстовые поля без null, лишние числа — строкой."""
    unions = _Unions(max_unions)
    compact = max_unions is not None
    props: dict[str, Any] = {}
    for f in schema.fields:
        if f.type == "table":
            cols = f.columns or []
            props[f.name] = {
                "type": "array",
                "description": f"{f.description} Пустой массив, если строк нет.".strip(),
                "items": {
                    "type": "object",
                    "properties": {c.name: _scalar(c, unions, compact) for c in cols},
                    "required": [c.name for c in cols],
                    "additionalProperties": False,
                },
            }
        else:
            props[f.name] = _scalar(f, unions, compact)
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
