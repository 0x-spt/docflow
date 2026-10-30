"""Подставной извлекатель для тестов и демо без модели: ответ по slug схемы."""
from typing import TYPE_CHECKING, Any

from docflow.extraction.base import Extraction
from docflow.schemas.model import DocSchema

if TYPE_CHECKING:
    from docflow.schemas.catalog import DocType


class FakeExtractor:
    model = "fake"

    def __init__(self, responses: dict[str, Any]):
        self.responses = responses
        self.calls: list[tuple[str, list[bytes]]] = []  # (slug, картинки) — тесты проверяют порядок страниц
        self.classify_calls: list[tuple[list[DocType], bytes]] = []  # (типы, первая страница)

    def extract(self, schema: DocSchema, images: list[bytes]) -> Extraction:
        self.calls.append((schema.slug, list(images)))
        return Extraction(raw=dict(self.responses[schema.slug]), model=self.model, input_tokens=0, output_tokens=0)

    def classify(self, types: "list[DocType]", image: bytes) -> str | None:
        self.classify_calls.append((list(types), image))
        return self.responses.get("_classify")
