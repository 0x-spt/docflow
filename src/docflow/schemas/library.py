"""Загрузка схем из YAML и встроенная библиотека документов РФ."""
from importlib import resources
from pathlib import Path

import yaml

from docflow.schemas.model import DocSchema


def _parse(text: str) -> DocSchema:
    return DocSchema.model_validate(yaml.safe_load(text))


def load_schema(path: Path) -> DocSchema:
    return _parse(Path(path).read_text(encoding="utf-8"))


def builtin_schemas() -> dict[str, DocSchema]:
    out: dict[str, DocSchema] = {}
    for entry in sorted(resources.files("docflow.schemas.builtin").iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".yaml"):
            schema = _parse(entry.read_text(encoding="utf-8"))
            out[schema.slug] = schema
    return out
