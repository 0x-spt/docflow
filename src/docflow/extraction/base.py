"""Общие типы извлечения и системный промпт."""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from docflow.schemas.llm import UNCERTAIN
from docflow.schemas.model import DocSchema

if TYPE_CHECKING:  # каталог тянет модели БД; во время работы нужны только типы
    from docflow.schemas.catalog import DocType

UNKNOWN = "unknown"


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

    def classify(self, types: "list[DocType]", image: bytes) -> str | None:
        """Slug типа документа по первой странице или None («не знаю»). Сбой — ExtractionError."""
        ...


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


def classify_prompt(types: "list[DocType]") -> str:
    lines = [f"- {t.slug}: {t.title}" + (f" — {t.description}" if t.description else "") for t in types]
    return (
        "Определи тип документа по изображению его первой страницы. Выбери ровно один slug из списка:\n"
        + "\n".join(lines)
        + f"\n\nЕсли документ не подходит ни под один тип или вы не уверены — верни «{UNKNOWN}». "
        "Не угадывай и ничего не выдумывай."
    )


def classify_schema(types: "list[DocType]") -> dict:
    return {
        "type": "object",
        "properties": {"slug": {"type": "string", "enum": [*(t.slug for t in types), UNKNOWN]}},
        "required": ["slug"],
        "additionalProperties": False,
    }


def parse_classification(raw: object) -> str | None:
    """Ответ модели {"slug": …} → slug или None («unknown»); остальное — invalid_json."""
    if not isinstance(raw, dict) or not isinstance(raw.get("slug"), str):
        raise ExtractionError("invalid_json", "ожидался объект со строкой slug")
    return None if raw["slug"] == UNKNOWN else raw["slug"]
