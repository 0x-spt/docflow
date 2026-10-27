"""Подставной извлекатель для тестов и демо без модели: ответ по slug схемы."""
from docflow.extraction.base import Extraction
from docflow.schemas.model import DocSchema


class FakeExtractor:
    model = "fake"

    def __init__(self, responses: dict[str, dict]):
        self.responses = responses
        self.calls: list[tuple[str, list[bytes]]] = []  # (slug, картинки) — тесты проверяют порядок страниц

    def extract(self, schema: DocSchema, images: list[bytes]) -> Extraction:
        self.calls.append((schema.slug, list(images)))
        return Extraction(raw=dict(self.responses[schema.slug]), model=self.model, input_tokens=0, output_tokens=0)
