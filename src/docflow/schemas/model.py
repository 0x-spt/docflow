"""Описание типа документа как данных (DocSchema). Правила названы здесь, реализуются в validation."""
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

FieldType = Literal["string", "integer", "number", "date", "boolean", "enum", "table"]
ColumnType = Literal["string", "integer", "number", "date", "boolean", "enum"]

# Правила одного поля (применяются к его значению) и правила документа (ссылаются на поля по имени).
FIELD_RULES = frozenset({"inn", "kpp", "ogrn", "snils", "bik", "regex", "date_not_future"})
DOC_RULES = frozenset({"table_sum_equals", "table_row_product", "date_order", "account_matches_bik"})


class RuleSpec(BaseModel):
    kind: str
    params: dict[str, Any] = Field(default_factory=dict)


class ColumnSpec(BaseModel):
    name: str
    type: ColumnType
    description: str = ""
    values: list[str] | None = None

    @model_validator(mode="after")
    def _enum_needs_values(self):
        if self.type == "enum" and not self.values:
            raise ValueError(f"«{self.name}»: для enum нужен непустой values")
        return self


class FieldSpec(ColumnSpec):
    type: FieldType
    required: bool = False
    columns: list[ColumnSpec] | None = None
    rules: list[RuleSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _table_needs_columns(self):
        if self.type == "table" and not self.columns:
            raise ValueError(f"«{self.name}»: для table нужен непустой columns")
        if self.type != "table" and self.columns:
            raise ValueError(f"«{self.name}»: columns допустимы только для table")
        return self


def _check_columns(names: list[str], what: str) -> None:
    if len(set(names)) != len(names):
        raise ValueError(f"{what}: имена должны быть уникальны")


# Какие параметры документных правил на что ссылаются: поля верхнего уровня / таблица / колонки таблицы.
_DOC_RULE_PARAMS: dict[str, dict[str, tuple[str, ...]]] = {
    "table_sum_equals": {"table": ("table",), "column": ("column",), "total": ("field",)},
    "table_row_product": {"table": ("table",), "qty": ("column",), "price": ("column",), "amount": ("column",)},
    "date_order": {"earlier": ("field",), "later": ("field",)},
    "account_matches_bik": {"account": ("field",), "bik": ("field",)},
}


def _check_field_rule(field: "FieldSpec", rule: RuleSpec) -> None:
    if rule.kind == "regex":
        pattern = rule.params.get("pattern")
        if not isinstance(pattern, str):
            raise ValueError(f"«{field.name}»: правилу regex нужен параметр pattern")
        try:
            re.compile(pattern)
        except re.error as e:
            raise ValueError(f"«{field.name}»: pattern не компилируется: {e}") from e


def _check_doc_rule(rule: RuleSpec, fields: dict[str, "FieldSpec"]) -> None:
    spec = _DOC_RULE_PARAMS[rule.kind]
    table: FieldSpec | None = None
    if "table" in spec:
        name = rule.params.get("table")
        table = fields.get(name) if isinstance(name, str) else None
        if table is None or table.type != "table":
            raise ValueError(f"{rule.kind}: table «{name}» не является полем-таблицей")
    for param, (kind,) in spec.items():
        if kind == "table":
            continue
        value = rule.params.get(param)
        if kind == "field" and value not in fields:
            raise ValueError(f"{rule.kind}: {param}=«{value}» — нет такого поля")
        if kind == "column" and value not in {c.name for c in (table.columns or [])}:
            raise ValueError(f"{rule.kind}: {param}=«{value}» — нет такой колонки в таблице «{table.name}»")


class DocSchema(BaseModel):
    slug: str = Field(pattern=r"^[a-z0-9_]{2,40}$")
    version: int = 1
    title: str
    description: str
    fields: list[FieldSpec]
    rules: list[RuleSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate(self):
        _check_columns([f.name for f in self.fields], "поля")
        for f in self.fields:
            if f.columns:
                _check_columns([c.name for c in f.columns], f"колонки «{f.name}»")
            for r in f.rules:
                if r.kind not in FIELD_RULES:
                    raise ValueError(f"«{f.name}»: неизвестное правило поля «{r.kind}»")
                _check_field_rule(f, r)
        by_name = {f.name: f for f in self.fields}
        for r in self.rules:
            if r.kind not in DOC_RULES:
                raise ValueError(f"неизвестное правило документа «{r.kind}»")
            _check_doc_rule(r, by_name)
        return self
