"""Извлечение через Claude: структурированный вывод по JSON Schema из DocSchema."""
import base64
import json

import anthropic

from docflow.extraction.base import Extraction, ExtractionError, system_prompt
from docflow.schemas.llm import to_json_schema
from docflow.schemas.model import DocSchema

_BETAS = ["server-side-fallback-2026-07-01"]
# Бюджет одного запроса должен укладываться в job_timeout воркера (300 с, см. worker.JOB_TIMEOUT):
# по умолчанию SDK ждёт до 10 минут и делает 2 повтора — задачу убил бы arq, а поток с запросом жил бы дальше.
# Один повтор оставлен для быстрых отказов (429/529/обрыв соединения); повтор после таймаута в бюджет
# не влезет — тогда документ закроет job_timeout как failed.
CLIENT_TIMEOUT = 240.0
CLIENT_MAX_RETRIES = 1


class ClaudeExtractor:
    def __init__(self, model: str = "claude-opus-5-5", client: anthropic.Anthropic | None = None, effort: str = "high"):
        self.model = model
        self.effort = effort
        self.client = client or anthropic.Anthropic(timeout=CLIENT_TIMEOUT, max_retries=CLIENT_MAX_RETRIES)

    def extract(self, schema: DocSchema, images: list[bytes]) -> Extraction:
        content: list[dict] = [
            {
                "type": "image",
                "source": {"type": "base64", "media_type": "image/jpeg", "data": base64.b64encode(img).decode()},
            }
            for img in images
        ]
        content.append({"type": "text", "text": f"Страниц: {len(images)}. Извлеки данные."})
        try:
            response = self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=system_prompt(schema),
                messages=[{"role": "user", "content": content}],
                output_config={
                    "effort": self.effort,
                    "format": {"type": "json_schema", "schema": to_json_schema(schema)},
                },
                betas=_BETAS,
                fallbacks="default",
            )
        except anthropic.APIError as e:
            # Auth/BadRequest — ошибки конфигурации, не временные: сообщение оставляем видимым.
            raise ExtractionError("api_error", str(e)) from e
        if response.stop_reason == "refusal":
            raise ExtractionError("refusal")
        if response.stop_reason in ("max_tokens", "model_context_window_exceeded"):
            raise ExtractionError("max_tokens")
        if response.stop_reason != "end_turn":
            raise ExtractionError("api_error", f"неожиданный stop_reason: {response.stop_reason}")
        text = next((b.text for b in response.content if b.type == "text"), None)
        if text is None:
            raise ExtractionError("invalid_json", "в ответе нет текстового блока")
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as e:
            raise ExtractionError("invalid_json", str(e)) from e
        if not isinstance(raw, dict):
            raise ExtractionError("invalid_json", "ожидался JSON-объект")
        # модель берём из ответа: fallback мог ответить другой — это правда для биллинга и аудита
        return Extraction(
            raw=raw,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
