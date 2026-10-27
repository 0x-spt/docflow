"""Адреса вебхуков тенанта. Секрет подписи показывается только при создании."""

import asyncio
import secrets
from typing import Any

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from docflow.api.deps import SessionDep, SettingsDep, TenantDep, parse_id
from docflow.db.models import WebhookEndpoint
from docflow.webhooks import EVENT_COMPLETED, EVENT_FAILED, check_webhook_url

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

EVENTS = (EVENT_COMPLETED, EVENT_FAILED)
NOT_FOUND = "вебхук не найден"


class WebhookIn(BaseModel):
    url: str = Field(description="Куда слать POST, http(s) на публичный адрес")
    events: list[str] = Field(description="Подмножество: document.completed, document.failed")


def _public(ep: WebhookEndpoint) -> dict[str, Any]:
    return {
        "id": str(ep.id),
        "url": ep.url,
        "events": list(ep.events),
        "active": ep.active,
        "created_at": ep.created_at.isoformat() if ep.created_at else None,
    }


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Подписаться на события",
    description=(
        "Регистрирует адрес для событий `document.completed` (статусы ok и needs_review) и `document.failed`. "
        "В ответе — секрет для проверки подписи `X-DocFlow-Signature`; он показывается один раз. "
        "422 — пустой или неизвестный список событий, URL не http(s) или ведёт на частный адрес."
    ),
)
async def create_webhook(
    body: WebhookIn, tenant_id: TenantDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    events = sorted(set(body.events))
    if not events or any(e not in EVENTS for e in events):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"events — непустой список из: {', '.join(EVENTS)}",
        )
    try:
        # getaddrinfo блокирующий: в отдельном потоке, чтобы не держать цикл событий
        await asyncio.to_thread(check_webhook_url, body.url, allow_private=settings.webhook_allow_private)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Недопустимый URL вебхука: {exc}") from None
    endpoint = WebhookEndpoint(
        tenant_id=tenant_id, url=body.url, secret=secrets.token_urlsafe(32), events=events, active=True
    )
    session.add(endpoint)
    await session.commit()
    return {**_public(endpoint), "secret": endpoint.secret}


@router.get(
    "",
    summary="Список вебхуков",
    description="Активные адреса тенанта. Секрет не возвращается.",
)
async def list_webhooks(tenant_id: TenantDep, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(WebhookEndpoint)
            .where(WebhookEndpoint.tenant_id == tenant_id, WebhookEndpoint.active.is_(True))
            .order_by(WebhookEndpoint.created_at)
        )
    ).scalars()
    return [_public(ep) for ep in rows]


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Отключить вебхук",
    description="Адрес перестаёт получать события; история доставок сохраняется. Чужой или несуществующий — 404.",
)
async def delete_webhook(webhook_id: str, tenant_id: TenantDep, session: SessionDep) -> Response:
    endpoint = await session.get(WebhookEndpoint, parse_id(webhook_id))
    if endpoint is None or endpoint.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    endpoint.active = False
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
