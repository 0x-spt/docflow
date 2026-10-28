"""Общее для уведомлений (Telegram, почта): заголовок документа, причины, текст сообщения."""

from docflow.connectors.base import ExportDoc
from docflow.connectors.crm_common import clean, counterparty_side, party, total_of

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


def reasons(doc: ExportDoc) -> str:
    messages = [m for i in doc.issues if (m := one_line(i.get("message")))]
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
        side = "supplier" if "supplier" in doc.data or "buyer" not in doc.data else "buyer"
        name = party(doc, counterparty_side({"counterparty": side})).name
        total = total_of(doc)
        extra = ", ".join(x for x in (name, f"сумма {money(total)}" if total is not None else "") if x)
        if extra:
            lines.append(extra)
    if doc.cabinet_url:
        lines.append(doc.cabinet_url)
    return clip("\n".join(lines))
