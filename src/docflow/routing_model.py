"""Схема правил маршрутизации (то, что хранится в routing_rules.rules и приходит в PUT /v1/routing-rules)."""

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Op = Literal["eq", "ne", "gt", "ge", "lt", "le", "contains", "in_dictionary", "empty", "not_empty"]

MAX_RULES = 100
MAX_CONDITIONS = 20


class Condition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # имя поля схемы документа
    field: str = Field(min_length=1, max_length=100)
    op: Op
    # для empty/not_empty не нужно; для in_dictionary — имя справочника
    value: str | float | None = None


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    name: str = Field(min_length=1, max_length=200)
    # slug схемы документа; None — любая. В JSON ключ называется «schema» (поле так не назвать: занято BaseModel)
    schema_slug: str | None = Field(default=None, alias="schema", max_length=100)
    conditions: list[Condition] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    export_to: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_RULES)
    require_approval: bool = False
    notify: list[uuid.UUID] = Field(default_factory=list, max_length=MAX_RULES)


class RulesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rules: list[Rule] = Field(max_length=MAX_RULES)
