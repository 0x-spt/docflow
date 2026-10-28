"""Подписанная доставка вебхуков: идемпотентное создание задач и ретраи с backoff."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import socket
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.config import get_settings
from docflow.db.models import Document, WebhookDelivery, WebhookEndpoint
from docflow.queue import DELIVER_WEBHOOK, Enqueuer
from docflow.serializers import document_exports, document_payload

RETRY_DELAYS = (10, 60, 300, 1800, 7200)
TIMEOUT = 10.0
MAX_ERROR = 500

EVENT_COMPLETED = "document.completed"
EVENT_FAILED = "document.failed"
EVENT_REVIEWED = "document.reviewed"  # человек подтвердил документ из needs_review


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def check_webhook_url(url: str, *, allow_private: bool = False) -> None:
    """Защита от SSRF: только http(s) и только публичные адреса. ValueError при нарушении."""
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise ValueError("схема URL должна быть http или https")
    host = parts.hostname
    if not host:
        raise ValueError("в URL нет хоста")
    if allow_private:
        return
    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if parts.scheme == "https" else 80))
    except OSError as exc:
        raise ValueError(f"хост не резолвится: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0].split("%")[0])
        # is_global отсекает всё, что IANA не считает глобально маршрутизируемым: CGNAT 100.64/10,
        # 192.0.0/24, бенчмарк 198.18/15, документационные сети и т. п. — список «запрещённых» не угнаться.
        # Мультикаст is_global не ловит (224.0.0.0/4 вне реестра special-purpose), поэтому отдельно.
        if not ip.is_global or ip.is_multicast:
            raise ValueError(f"адрес {ip} не публичный")


def _job_id(delivery_id: UUID, attempt: int) -> str:
    return f"deliver:{delivery_id}:{attempt}"


def _event_for(document: Document) -> str:
    if document.status in ("ok", "needs_review"):
        return EVENT_COMPLETED
    if document.status == "failed":
        return EVENT_FAILED
    raise ValueError(f"документ в статусе {document.status} не порождает события")


async def enqueue_webhooks(
    session: AsyncSession, document: Document, enqueuer: Enqueuer, *, event: str | None = None
) -> int:
    """Создаёт доставки для подходящих endpoint'ов; уже существующие пропускает. Возвращает число новых."""
    event = event or _event_for(document)
    # подписчик на completed получает и reviewed: для него это тоже «документ готов»
    wanted = {event, EVENT_COMPLETED} if event == EVENT_REVIEWED else {event}
    endpoints = (
        await session.execute(
            select(WebhookEndpoint).where(
                WebhookEndpoint.tenant_id == document.tenant_id, WebhookEndpoint.active.is_(True)
            )
        )
    ).scalars().all()

    created: list[UUID] = []
    for ep in endpoints:
        if not wanted & set(ep.events or []):
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
        await enqueuer.enqueue(DELIVER_WEBHOOK, delivery_id, job_id=_job_id(delivery_id, 0))
    return len(created)


async def claim_attempt(session: AsyncSession, delivery_id: UUID, seen: int) -> bool:
    """Атомарно занимает попытку: для данного seen rowcount 1 получит только один воркер."""
    res = await session.execute(
        update(WebhookDelivery)
        .where(
            WebhookDelivery.id == delivery_id,
            WebhookDelivery.status == "pending",
            WebhookDelivery.attempt == seen,
        )
        .values(attempt=seen + 1)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return res.rowcount == 1


async def requeue_stale(
    session: AsyncSession, enqueuer: Enqueuer, now: datetime, grace: timedelta = timedelta(minutes=5)
) -> int:
    """Возвращает в очередь pending-доставки, потерянные при падении между commit и enqueue."""
    cutoff = now - grace
    rows = (
        await session.execute(
            select(WebhookDelivery.id, WebhookDelivery.attempt).where(
                WebhookDelivery.status == "pending",
                (WebhookDelivery.next_attempt_at < cutoff)
                | (WebhookDelivery.next_attempt_at.is_(None) & (WebhookDelivery.created_at < cutoff)),
            )
        )
    ).all()
    for delivery_id, attempt in rows:
        await enqueuer.enqueue(DELIVER_WEBHOOK, delivery_id, job_id=_job_id(delivery_id, attempt))
    return len(rows)


async def deliver(
    session: AsyncSession,
    delivery_id: UUID,
    http: httpx.AsyncClient,
    enqueuer: Enqueuer,
    *,
    allow_private: bool | None = None,
) -> None:
    delivery = await session.get(WebhookDelivery, delivery_id)
    if delivery is None or delivery.status != "pending":
        return
    endpoint = await session.get(WebhookEndpoint, delivery.endpoint_id)
    document = await session.get(Document, delivery.document_id)
    problem = None
    if endpoint is None or not endpoint.active:
        problem = "endpoint удалён или выключен"
    elif document is None:
        problem = "документ удалён"
    if problem:
        delivery.status = "failed"
        delivery.last_error = problem
        delivery.next_attempt_at = None
        await session.commit()
        return

    if not await claim_attempt(session, delivery_id, delivery.attempt):
        return  # попытку занял другой воркер
    await session.refresh(delivery)
    if allow_private is None:
        allow_private = get_settings().webhook_allow_private

    payload = {
        "event": delivery.event,
        "document": document_payload(
            document, get_settings().public_base_url, await document_exports(session, document.id)
        ),
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
        # проверка прямо перед отправкой закрывает DNS rebinding между регистрацией и доставкой
        await asyncio.to_thread(check_webhook_url, endpoint.url, allow_private=allow_private)
        resp = await http.post(endpoint.url, content=body, headers=headers, timeout=TIMEOUT)
        delivery.response_code = resp.status_code
        if not resp.is_success:
            error = f"HTTP {resp.status_code}: {resp.text}"
    except ValueError as exc:
        delivery.response_code = None
        error = f"URL отклонён: {exc}"
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
    attempt = delivery.attempt  # уже увеличен claim'ом: 1 первая попытка + len(RETRY_DELAYS) ретраев
    if attempt > len(RETRY_DELAYS):
        delivery.status = "failed"
        delivery.next_attempt_at = None
        await session.commit()
        return
    delay = RETRY_DELAYS[attempt - 1]
    delivery.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
    await session.commit()  # до постановки в очередь
    await enqueuer.enqueue(DELIVER_WEBHOOK, delivery.id, defer_by=delay, job_id=_job_id(delivery.id, attempt))
