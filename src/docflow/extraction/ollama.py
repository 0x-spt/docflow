"""Извлечение через локальную VLM в Ollama (httpx, без SDK)."""
import base64
import json

import httpx

from docflow.extraction.base import Extraction, ExtractionError, system_prompt
from docflow.schemas.llm import to_json_schema
from docflow.schemas.model import DocSchema

_TIMEOUT = 300.0  # локальная 7B VLM медленная


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
