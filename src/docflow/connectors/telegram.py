"""Telegram: уведомления бота в чат или канал.

Токен бота сидит в пути URL Bot API, поэтому хранится как секрет и не попадает ни в ошибки, ни в логи:
исключения httpx (в них URL) мы не пробрасываем и не логируем, наружу — только тип ошибки.
"""

from typing import Any

import httpx

from docflow.connectors.base import REGISTRY, BaseConnector, ConnectorError, DestCtx, ExportDoc, FieldDef
from docflow.connectors.notify_common import PURPOSES, notification_text, one_line

API = "https://api.telegram.org"


def _token(ctx: DestCtx) -> str:
    token = str(ctx.secrets.get("bot_token") or "").strip()
    if not token:
        raise ConnectorError("не указан токен бота Telegram")
    return token


def _chat_id(ctx: DestCtx) -> str:
    chat = str(ctx.config.get("chat_id") or "").strip()
    if not chat:
        raise ConnectorError("не указан чат Telegram")
    return chat


async def _call(ctx: DestCtx, method: str, payload: dict[str, Any]) -> Any:
    chat = str(payload.get("chat_id", ""))
    try:
        resp = await ctx.http.post(f"{API}/bot{_token(ctx)}/{method}", json=payload)
    except httpx.HTTPError as exc:
        raise ConnectorError(f"Telegram не ответил ({type(exc).__name__})", retryable=True) from None
    code = resp.status_code
    try:
        body = resp.json()
    except ValueError:
        body = None
    description = one_line(body.get("description"))[:200].lower() if isinstance(body, dict) else ""
    if code == 429 or code >= 500:
        raise ConnectorError(f"Telegram временно недоступен или ограничил запросы (код {code})", retryable=True)
    if code == 401:
        raise ConnectorError("токен бота не принят")
    if code in (400, 403) and ("chat" in description or "bot" in description or code == 403):
        raise ConnectorError(
            f"бот не может писать в чат {chat} — добавьте бота в чат и отправьте ему сообщение"
            if chat
            else "Telegram отклонил запрос"
        )
    if code >= 400 or not isinstance(body, dict) or not body.get("ok"):
        raise ConnectorError(f"Telegram отклонил запрос {method} (код {code})")
    return body.get("result")


class Telegram(BaseConnector):
    kind = "telegram"
    label = "Telegram"
    description = "уведомления о проверке и новых документах"
    config_fields = [
        FieldDef(
            "chat_id",
            "Чат",
            required=True,
            help="Числовой ID чата или @имя канала. Бот должен быть добавлен в чат (в канал — администратором).",
        ),
    ]
    secret_fields = [FieldDef("bot_token", "Токен бота", required=True, help="Выдаёт @BotFather.")]

    async def test(self, ctx: DestCtx) -> str | None:
        me = await _call(ctx, "getMe", {})
        username = one_line(me.get("username")) if isinstance(me, dict) else ""
        chat_id = _chat_id(ctx)
        chat = await _call(ctx, "getChat", {"chat_id": chat_id})
        title = ""
        if isinstance(chat, dict):
            title = one_line(chat.get("title") or chat.get("username") or chat.get("first_name"))
        return f"Бот @{username} доступен, чат «{title or chat_id}» найден"

    async def notify(self, ctx: DestCtx, doc: ExportDoc, purpose: str) -> None:
        if purpose not in PURPOSES:
            raise ConnectorError(f"«{self.label}» не умеет это назначение: {purpose}")
        await _call(
            ctx,
            "sendMessage",
            {
                "chat_id": _chat_id(ctx),
                "text": notification_text(doc, purpose),
                "disable_web_page_preview": True,
            },
        )


REGISTRY["telegram"] = Telegram()
