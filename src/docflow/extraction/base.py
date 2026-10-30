"""Общие типы извлечения и системный промпт."""
from dataclasses import dataclass
from typing import Protocol

from docflow.schemas.llm import UNCERTAIN
from docflow.schemas.model import DocSchema


@dataclass(frozen=True)
class Extraction:
    raw: dict
    model: str
    input_tokens: int
    output_tokens: int


class ExtractionError(Exception):
    """reason: refusal | max_tokens | invalid_json | api_error."""

    def __init__(self, reason: str, message: str = ""):
        super().__init__(f"{reason}: {message}" if message else reason)
        self.reason = reason
        self.message = message


class Extractor(Protocol):
    model: str

    def extract(self, schema: DocSchema, images: list[bytes]) -> Extraction: ...


_RULES = f"""Ты распознаёшь документы по изображениям страниц и возвращаешь данные строго по заданной JSON-схеме.
Документы бывают отсканированными, перекошенными, с рукописными вставками.

Правила:
- Записывай то, что написано, а не то, что «должно быть». Ничего не пересчитывай и не исправляй: \
итоги, суммы и ставки бери как напечатаны, даже если они не сходятся.
- Если поля в документе нет или оно пустое (прочерк) — верни null (если схема поля не допускает null — \
пустую строку), для таблицы — пустой массив. Ничего не выдумывай.
- Зачёркнутое значение игнорируй, бери то, что написано рядом взамен.
- Если поле видно, но прочитать его уверенно нельзя (размыто, перекрыто, неразборчиво) — \
верни для него null (или пустую строку) и внеси имя поля в «{UNCERTAIN}».
- Несколько страниц — это один документ: собери данные со всех страниц в один ответ, \
строки таблицы с разных страниц объединяй в порядке следования.
- Даты возвращай в формате ГГГГ-ММ-ДД. Числа — без пробелов и знаков валюты, дробная часть через точку."""


def system_prompt(schema: DocSchema) -> str:
    """Детерминированный (без дат и случайных частей) — иначе не работает кеш промпта."""
    return f"{_RULES}\n\nТип документа: {schema.title}.\n{schema.description}"
