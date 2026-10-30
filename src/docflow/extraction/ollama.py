"""Извлечение через локальную VLM в Ollama (httpx, без SDK)."""
import base64
import json
from typing import TYPE_CHECKING

import httpx

from docflow.extraction.base import (
    Extraction,
    ExtractionError,
    classify_prompt,
    classify_schema,
    parse_classification,
    system_prompt,
)
from docflow.schemas.llm import to_json_schema
from docflow.schemas.model import DocSchema

if TYPE_CHECKING:
    from docflow.schemas.catalog import DocType

# локальная VLM медленная, но ответ обязан успеть до job_timeout воркера (480 с, см. worker.JOB_TIMEOUT)
# вместе с подготовкой страниц и OCR; без повторов
_TIMEOUT = 240.0
# держать модель загруженной между документами: загрузка крупной модели — минуты и съела бы таймаут
_KEEP_ALIVE = "1h"
# определение типа — первый вызов, а при холодном старте загрузка модели занимает минуты
_CLASSIFY_TIMEOUT = 180.0


class OllamaExtractor:
    def __init__(self, url: str, model: str, http: httpx.Client | None = None):
        self.url = url.rstrip("/")
        self.model = model
        self.http = http or httpx.Client(timeout=_TIMEOUT)

    def extract(self, schema: DocSchema, images: list[bytes]) -> Extraction:
        payload = {
            "model": self.model,
            "stream": False,
            "format": to_json_schema(schema),
            "messages": [
                {"role": "system", "content": system_prompt(schema)},
                {
                    "role": "user",
                    "content": f"Страниц: {len(images)}. Извлеки данные.",
                    "images": [base64.b64encode(i).decode() for i in images],
                },
            ],
            "options": {"temperature": 0},
            "keep_alive": _KEEP_ALIVE,
        }
        try:
            r = self.http.post(f"{self.url}/api/chat", json=payload, timeout=_TIMEOUT)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ExtractionError("api_error", str(e)) from e
        try:
            raw = json.loads(body["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            raise ExtractionError("invalid_json", str(e)) from e
        if not isinstance(raw, dict):
            raise ExtractionError("invalid_json", "ожидался JSON-объект")
        return Extraction(
            raw=raw,
            model=self.model,
            input_tokens=int(body.get("prompt_eval_count") or 0),
            output_tokens=int(body.get("eval_count") or 0),
        )

    def classify(self, types: "list[DocType]", image: bytes) -> str | None:
        payload = {
            "model": self.model,
            "stream": False,
            "format": classify_schema(types),
            "messages": [
                {"role": "system", "content": classify_prompt(types)},
                {"role": "user", "content": "Определи тип документа.", "images": [base64.b64encode(image).decode()]},
            ],
            "options": {"temperature": 0},
            "keep_alive": _KEEP_ALIVE,
        }
        try:
            r = self.http.post(f"{self.url}/api/chat", json=payload, timeout=_CLASSIFY_TIMEOUT)
            r.raise_for_status()
            body = r.json()
        except (httpx.HTTPError, ValueError) as e:
            raise ExtractionError("api_error", str(e)) from e
        try:
            return parse_classification(json.loads(body["message"]["content"]))
        except (KeyError, TypeError, json.JSONDecodeError) as e:
            raise ExtractionError("invalid_json", str(e)) from e
