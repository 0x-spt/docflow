"""Описание типа документа как данных (DocSchema). Правила названы здесь, реализуются в validation."""
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
        for r in self.rules:
            if r.kind not in DOC_RULES:
                raise ValueError(f"неизвестное правило документа «{r.kind}»")
        return self
