"""Общее для уведомлений (Telegram, почта): заголовок документа, причины, текст сообщения."""

from docflow.connectors.base import ExportDoc
from docflow.connectors.crm_common import clean, party, total_of
from docflow.labels import total_field
from docflow.validation.engine import INFO_CODES

TEXT_MAX = 4000  # у Telegram потолок 4096; тот же запас, что у уведомлений о заявках
REASONS_SHOWN = 3
PURPOSES = ("notify_review", "notify_new")


def one_line(value: object) -> str:
    """Недоверенный текст в одну строку: без переводов строк и управляющих символов (годится для заголовков писем)."""
    return " ".join(clean(value).split())


def doc_ref(doc: ExportDoc) -> str:
    """«Счёт № 42 от 05.10.2026»"""
    parts = [one_line(doc.title) or "Документ"]
    if doc.number:
        parts.append(f"№ {one_line(doc.number)}")
    if doc.issued:
        parts.append(f"от {doc.issued:%d.%m.%Y}")
    return " ".join(parts)


def money(value: float) -> str:
    return f"{value:,.2f}".replace(",", " ").replace(".", ",") + " ₽"


def field_title(doc: ExportDoc, key: str) -> str | None:
    """Название компании для поля верхнего уровня: отличается от штатного — оно, иначе None."""
    now = next((f.title for f in doc.schema.fields if f.name == key), None)
    was = next((f.title for f in doc.default_schema.fields if f.name == key), None)
    return now if now and now != was else None


def _reason(doc: ExportDoc, issue: dict) -> str:
    message = one_line(issue.get("message"))
    label = field_title(doc, str(issue.get("field") or ""))
    return f"{one_line(label)}: {message}" if message and label else message


def reasons(doc: ExportDoc) -> str:
    messages = [m for i in doc.issues if i.get("code") not in INFO_CODES and (m := _reason(doc, i))]
    shown = "; ".join(messages[:REASONS_SHOWN])
    if len(messages) > REASONS_SHOWN:
        shown += f" и ещё {len(messages) - REASONS_SHOWN}"
    return shown


def clip(text: str, limit: int = TEXT_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"


def notification_text(doc: ExportDoc, purpose: str) -> str:
    if purpose == "notify_review":
        lines = [f"Нужна проверка: {doc_ref(doc)}"]
        if why := reasons(doc):
            lines.append(f"Причины: {why}")
    else:
        lines = [f"Новый документ: {doc_ref(doc)}"]
        name = party(doc, "supplier").name or party(doc, "buyer").name
        total = total_of(doc)
        key = total_field(doc.default_schema)
        title = field_title(doc, key) if key else None
        # money(None) падал TypeError у типов без суммы: строку собираем только при наличии числа
        amount = "" if total is None else (f"{title}: {money(total)}" if title else f"сумма {money(total)}")
        extra = ", ".join(x for x in (name, amount) if x)
        if extra:
            lines.append(extra)
    if doc.cabinet_url:
        lines.append(doc.cabinet_url)
    return clip("\n".join(lines))
