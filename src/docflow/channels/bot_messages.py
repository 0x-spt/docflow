"""Что бот пишет в чат о документе. Одна отправка — один вызов Bot API: сбой не оставит половины сообщения."""

from datetime import date

from docflow.channels.botapi import BotApi
from docflow.connectors.base import ExportDoc
from docflow.connectors.notify_common import TEXT_MAX, clip, doc_ref, first_reason, money, notification_text, one_line
from docflow.db.models import Document
from docflow.labels import total_field
from docflow.serializers import digital_filename

CAPTION_MAX = 1024  # потолок подписи к файлу у Telegram
# в заголовке и так есть номер и дата, отдельными строками не повторяем
HEADER_FIELDS = ("number", "issued")
MODEL_ERROR_PREFIX = "Не удалось извлечь данные"
MODEL_ERROR_TEXT = "Не удалось распознать документ: модель не ответила. Попробуйте прислать его ещё раз"
FAILED_TEXT = "Не удалось распознать документ"
APPROVED_TEXT = "Документ проверен и подтверждён"
REJECTED_TEXT = "Документ отклонён"
# схема удалена — подписей и заголовка нет, остаётся сказать главное
READY_NO_SCHEMA = "Документ распознан"
REVIEW_NO_SCHEMA = "Нужна проверка"


def _with_link(lines: list[str], doc: ExportDoc | None, limit: int) -> str:
    """Режется только текст: ссылка в кабинет остаётся целой при любой длине значений."""
    link = f"Открыть в кабинете: {doc.cabinet_url}" if doc and doc.cabinet_url else ""
    if not link:
        return clip("\n".join(lines), limit)
    body = clip("\n".join(lines), limit - len(link) - 1)
    return f"{body}\n{link}"


def _value(doc: ExportDoc, key: str) -> str:
    value = doc.data.get(key)
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return ""
    if key == total_field(doc.default_schema) and not isinstance(value, str):
        return money(value)
    spec = next((f for f in doc.schema.fields if f.name == key), None)
    if spec is not None and spec.type == "date" and isinstance(value, str):
        try:
            return f"{date.fromisoformat(value):%d.%m.%Y}"
        except ValueError:
            pass
    return one_line(value)


def result_text(doc: ExportDoc, queue_fields: list[str]) -> str:
    lines = [doc_ref(doc)]
    titles = {f.name: f.title or f.name for f in doc.schema.fields}
    for key in queue_fields:
        if key not in HEADER_FIELDS and (value := _value(doc, key)):
            lines.append(f"{one_line(titles.get(key, key))}: {value}")
    return _with_link(lines, doc, CAPTION_MAX)


def review_text(doc: ExportDoc) -> str:
    lines = [f"Нужна проверка: {doc_ref(doc)}"]
    if why := first_reason(doc):
        lines.append(why)
    return _with_link(lines, doc, CAPTION_MAX)


def failed_text(error: str | None, doc: ExportDoc | None) -> str:
    if error is None:
        text = FAILED_TEXT
    elif error.startswith(MODEL_ERROR_PREFIX):
        # в скобках — сырой ответ API модели: человеку в чате он не нужен
        text = MODEL_ERROR_TEXT
    else:
        text = f"{FAILED_TEXT}: {error}"
    return _with_link([text], doc, TEXT_MAX)


def reviewed_text(doc: ExportDoc, status: str) -> str:
    return clip(f"{APPROVED_TEXT if status == 'ok' else REJECTED_TEXT}: {doc_ref(doc)}")


def _text_without_schema(document: Document, purpose: str) -> str:
    if purpose == "bot_reviewed":
        return APPROVED_TEXT if document.status == "ok" else REJECTED_TEXT
    if document.status == "failed":
        return failed_text(document.error, None)
    return READY_NO_SCHEMA if document.status == "ok" else REVIEW_NO_SCHEMA


async def send_bot_export(
    api: BotApi,
    chat_id: int,
    reply_to: int | None,
    doc: ExportDoc | None,
    document: Document,
    purpose: str,
    *,
    full: bool,
    queue_fields: list[str],
) -> None:
    """full — чат, из которого прислан документ: ему реплай и PDF; остальным короткое уведомление."""
    reply = reply_to if full else None
    if doc is None:
        return await api.send_message(chat_id, _text_without_schema(document, purpose), reply)
    if purpose == "bot_reviewed":
        return await api.send_message(chat_id, reviewed_text(doc, document.status), reply)
    if document.status == "failed":
        return await api.send_message(chat_id, failed_text(document.error, doc), reply)
    approved = document.status == "ok"
    if not full:
        return await api.send_message(chat_id, notification_text(doc, "notify_new" if approved else "notify_review"))
    caption = result_text(doc, queue_fields) if approved else review_text(doc)
    pdf = await doc.load_digital_pdf()
    if pdf is None:
        return await api.send_message(chat_id, caption, reply)
    await api.send_document(chat_id, digital_filename(document), pdf, caption, reply)
