"""Подписанная доставка вебхуков: идемпотентное создание задач и ретраи с backoff."""

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.config import get_settings
from docflow.db.models import Document, WebhookDelivery, WebhookEndpoint
from docflow.queue import DELIVER_WEBHOOK, Enqueuer
from docflow.serializers import document_payload

RETRY_DELAYS = (10, 60, 300, 1800, 7200)
TIMEOUT = 10.0
MAX_ERROR = 500

EVENT_COMPLETED = "document.completed"
EVENT_FAILED = "document.failed"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _event_for(document: Document) -> str:
    if document.status in ("ok", "needs_review"):
        return EVENT_COMPLETED
    if document.status == "failed":
        return EVENT_FAILED
    raise ValueError(f"документ в статусе {document.status} не порождает события")


async def enqueue_webhooks(session: AsyncSession, document: Document, enqueuer: Enqueuer) -> int:
    """Создаёт доставки для подходящих endpoint'ов; уже существующие пропускает. Возвращает число новых."""
    event = _event_for(document)
    endpoints = (
        await session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == document.tenant_id, WebhookEndpoint.active.is_(True)
            )
        )
    ).scalars().all()

    created: list[UUID] = []
    for ep in endpoints:
        if event not in (ep.events or []):
            continue
        exists = (
            await session.execute(
                select(WebhookDelivery.id).where(
                    WebhookDelivery.endpoint_id == ep.id,
                    WebhookDelivery.document_id == document.id,
                    WebhookDelivery.event == event,
                )
            )
        ).first()
        if exists:
            continue
        delivery = WebhookDelivery(endpoint_id=ep.id, document_id=document.id, event=event)
        try:
            async with session.begin_nested():
                session.add(delivery)
        except IntegrityError:  # гонка с параллельным вызовом
            continue
        created.append(delivery.id)
    await session.commit()
    for delivery_id in created:  # после commit, чтобы воркер увидел строку
        await enqueuer.enqueue(DELIVER_WEBHOOK, delivery_id)
    return len(created)


async def deliver(session: AsyncSession, delivery_id: UUID, http: httpx.AsyncClient, enqueuer: Enqueuer) -> None:
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None or delivery.status != "pending":
        return
    endpoint = await session.get(WebhookEndpoint, delivery.endpoint_id)
    document = await session.get(Document, delivery.document_id)
    if endpoint is None or not endpoint.active or document is None:
        delivery.status = "failed"
        delivery.last_error = "endpoint удалён или выключен"
        delivery.next_attempt_at = None
        await session.commit()
        return

    payload = {
        "event": delivery.event,
        "document": document_payload(document, get_settings().public_base_url),
    }
    # подписываем и отправляем ровно эти байты
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode()
    headers = {
        "Content-Type": "application/json",
        "X-DocFlow-Signature": sign(endpoint.secret, body),
        "X-DocFlow-Event": delivery.event,
        "X-DocFlow-Delivery": str(delivery.id),
    }

    error: str | None = None
    try:
        resp = await http.post(endpoint.url, content=body, headers=headers, timeout=TIMEOUT)
        delivery.response_code = resp.status_code
        if not resp.is_success:
            error = f"HTTP {resp.status_code}: {resp.text}"
    except httpx.HTTPError as exc:
        delivery.response_code = None
        error = f"{type(exc).__name__}: {exc}"

    if error is None:
        delivery.status = "delivered"
        delivery.last_error = None
        delivery.next_attempt_at = None
        await session.commit()
        return

    delivery.last_error = error[:MAX_ERROR]
    delivery.attempt += 1
    if delivery.attempt >= len(RETRY_DELAYS):
        delivery.status = "failed"
        delivery.next_attempt_at = None
        await session.commit()
        return
    delay = RETRY_DELAYS[delivery.attempt - 1]
    delivery.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
    await session.commit()  # до постановки в очередь
    await enqueuer.enqueue(DELIVER_WEBHOOK, delivery.id, defer_by=delay)
