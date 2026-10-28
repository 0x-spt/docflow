"""Уведомления оператору о новых заявках с сайта (Telegram)."""

import logging

import httpx

from docflow.config import Settings
from docflow.db.models import Lead

logger = logging.getLogger(__name__)

TIMEOUT = 5.0

_LABELS = (
    ("name", "Имя"),
    ("company", "Компания"),
    ("contact", "Контакт"),
    ("niche", "Ниша"),
    ("volume", "Объём в месяц"),
    ("documents", "Документы"),
    ("message", "Сообщение"),
)


def lead_text(lead: Lead) -> str:
    """Текст уведомления: все непустые поля заявки, без IP."""
    lines = ["Новая заявка на аудит"]
    for attr, label in _LABELS:
        value = getattr(lead, attr)
        if value:
            lines.append(f"{label}: {value}")
    return "\n".join(lines)


async def notify_lead(lead: Lead, settings: Settings) -> bool:
    """Отправить заявку в Telegram. Любая ошибка -> False и запись в лог; без настроек запроса нет."""
    if not settings.leads_tg_token or not settings.leads_tg_chat:
        return False
    url = f"https://api.telegram.org/bot{settings.leads_tg_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            resp = await client.post(url, json={"chat_id": settings.leads_tg_chat, "text": lead_text(lead)})
        resp.raise_for_status()
    except Exception as e:  # токен сидит в URL: в лог только тип ошибки и код, без самого URL
        code = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
        logger.warning("не удалось отправить заявку %s в Telegram: %s (код %s)", lead.id, type(e).__name__, code)
        return False
    return True
