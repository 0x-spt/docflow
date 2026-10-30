"""Выгрузки и уведомления получателям: идемпотентная постановка, доставка через коннектор, ретраи с backoff.

Одна строка exports на (документ, получатель, назначение): повторная постановка (повтор конвейера, двойное
одобрение) дубля не создаёт, `done` автоматически не переотправляется — только ручное «Повторить» для `failed`.
Выгрузка в учётную систему (purpose=export) уходит только для документа в `ok`: needs_review/failed туда не
попадают, даже если задача как-то оказалась в очереди.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.channels.bot_messages import (
    CHAT_ERROR_PREFIX,
    SEND_ERROR_PREFIX,
    chat_unavailable_text,
    last_error_about_chat,
    send_bot_export,
)
from docflow.channels.botapi import TOKEN_REVOKED, BotApi, BotApiError, BotChatUnavailable, BotTokenRevoked
from docflow.config import Settings
from docflow.connectors.base import REGISTRY, Connector, ConnectorError, DestCtx, ExportDoc, supports
from docflow.db.models import BOT_PURPOSES, BotChat, Destination, Document, Export, TenantBot
from docflow.labels import apply_labels, get_label_set, queue_fields_for
from docflow.pdf.digital import render_digital_pdf
from docflow.queue import DELIVER_EXPORT, Enqueuer
from docflow.routing import RoutingDecision
from docflow.schemas.catalog import AUTO_SLUG
from docflow.schemas.model import DocSchema
from docflow.secrets import SecretsKeyMissing, decrypt_secrets
from docflow.storage.blobs import BlobNotFound, BlobStore, scan_key
from docflow.webhooks import UnresolvableHost, check_webhook_url

logger = logging.getLogger(__name__)

# как у вебхуков: 1 попытка + 5 повторов (10 с, 1 мин, 5 мин, 30 мин, 2 ч)
RETRY_DELAYS = (10, 60, 300, 1800, 7200)
# пока попытка идёт, next_attempt_at сдвинут на LEASE вперёд: крон не поставит вторую копию работающей задачи
# (дубль во внешней системе). Больше job_timeout воркера (480 с) с запасом.
LEASE = timedelta(minutes=10)
MAX_ERROR = 500
# дедлайн работы коннектора за попытку: меньше job_timeout воркера (480 с), чтобы успеть записать итог
CONNECTOR_DEADLINE = 240.0
# задача с номером попытки меньше текущего — дубль, если попытка занята/отложена дальше этого запаса
LEASE_SLACK = timedelta(seconds=5)

DESTINATION_OFF = "получатель отключён"
DOCUMENT_GONE = "документ удалён"
NOT_APPROVED = "документ не подтверждён (статус {status}): в учётную систему не выгружается"
STALE_NOTIFICATION = "документ уже в другом статусе — уведомление не актуально"
ATTEMPTS_EXHAUSTED = "превышено число попыток"
TIMED_OUT = "внешняя система не ответила вовремя"

BOT_CHAT_GONE = "чат отвязан"
BOT_OFF = "бот отключён"

PURPOSES = ("export", "notify_review", "notify_new")


def export_job_id(export_id: UUID | str, attempt: int) -> str:
    return f"export:{export_id}:{attempt}"


async def enqueue_exports(
    session: AsyncSession, document: Document, purpose: str, destination_ids, enqueuer: Enqueuer
) -> int:
    """Создаёт выгрузки документа активным получателям тенанта, чей коннектор умеет это назначение.

    Уже существующие (в любом статусе) пропускает; мёртвые/чужие id молча отбрасывает. Возвращает число новых.
    """
    ids = set(destination_ids)
    if not ids:
        return 0
    document_id, tenant_id = document.id, document.tenant_id
    destinations = (
        await session.execute(
            select(Destination)
            .where(Destination.tenant_id == tenant_id, Destination.id.in_(ids), Destination.active.is_(True))
            .order_by(Destination.created_at, Destination.id)
        )
    ).scalars().all()

    created: list[UUID] = []
    for dest in destinations:
        connector = REGISTRY.get(dest.kind)
        if connector is None or not supports(connector, purpose):
            continue
        exists = (
            await session.execute(
                select(Export.id).where(
                    Export.document_id == document_id, Export.destination_id == dest.id, Export.purpose == purpose
                )
            )
        ).first()
        if exists:
            continue
        export = Export(tenant_id=tenant_id, document_id=document_id, destination_id=dest.id, purpose=purpose)
        try:
            async with session.begin_nested():
                session.add(export)
        except IntegrityError:  # гонка с параллельной постановкой
            continue
        created.append(export.id)
    await session.commit()
    for export_id in created:  # после commit, чтобы воркер увидел строку; потерянное поднимет requeue_stale
        await enqueuer.enqueue(DELIVER_EXPORT, str(export_id), 0, job_id=export_job_id(export_id, 0))
    return len(created)


async def enqueue_routed(
    session: AsyncSession, document: Document, decision: RoutingDecision, enqueuer: Enqueuer
) -> int:
    """По итоговому статусу: ok → выгрузки и «новый документ»; needs_review → «на проверку»; прочее — ничего."""
    status = document.status
    if status == "ok":
        n = await enqueue_exports(session, document, "export", decision.export_to, enqueuer)
        return n + await enqueue_exports(session, document, "notify_new", decision.notify, enqueuer)
    if status == "needs_review":
        return await enqueue_exports(session, document, "notify_review", decision.notify, enqueuer)
    return 0


def _own_source(document: Document, bot_id: UUID) -> dict | None:
    """source_ref документа, если он прислан в этого бота; прислан в прежнего — как любой другой канал."""
    ref = document.source_ref
    if document.source == "telegram" and isinstance(ref, dict) and ref.get("bot_id") == str(bot_id):
        return ref
    return None


async def enqueue_bot_messages(session: AsyncSession, document: Document, purpose: str, enqueuer: Enqueuer) -> int:
    """Сообщения бота о документе: присланный в бота — только исходному чату, остальные — чатам с all_documents.

    Нет активного бота или нужного чата — ничего не ставится. Уже существующие пропускает, возвращает число новых.
    """
    document_id, tenant_id = document.id, document.tenant_id
    bot_id = (
        await session.execute(select(TenantBot.id).where(TenantBot.tenant_id == tenant_id, TenantBot.active.is_(True)))
    ).scalar_one_or_none()
    if bot_id is None:
        return 0
    ref = _own_source(document, bot_id)
    chats = select(BotChat.id).where(BotChat.bot_id == bot_id).order_by(BotChat.linked_at, BotChat.id)
    chats = chats.where(BotChat.chat_id == ref.get("chat_id") if ref else BotChat.all_documents.is_(True))

    created: list[UUID] = []
    for chat_id in (await session.execute(chats)).scalars().all():
        exists = (
            await session.execute(
                select(Export.id).where(
                    Export.document_id == document_id, Export.bot_chat_id == chat_id, Export.purpose == purpose
                )
            )
        ).first()
        if exists:
            continue
        export = Export(tenant_id=tenant_id, document_id=document_id, bot_chat_id=chat_id, purpose=purpose)
        try:
            async with session.begin_nested():
                session.add(export)
        except IntegrityError:  # гонка с параллельной постановкой
            continue
        created.append(export.id)
    await session.commit()
    for export_id in created:  # после commit, чтобы воркер увидел строку; потерянное поднимет requeue_stale
        await enqueuer.enqueue(DELIVER_EXPORT, str(export_id), 0, job_id=export_job_id(export_id, 0))
    return len(created)


async def requeue_stale_exports(
    session: AsyncSession, enqueuer: Enqueuer, now: datetime, grace: timedelta = timedelta(minutes=5)
) -> int:
    """Возвращает в очередь pending-выгрузки, потерянные при падении между commit и enqueue или воркера."""
    cutoff = now - grace
    rows = (
        await session.execute(
            select(Export.id, Export.attempt).where(
                Export.status == "pending",
                (Export.next_attempt_at < cutoff) | (Export.next_attempt_at.is_(None) & (Export.created_at < cutoff)),
            )
        )
    ).all()
    requeued = 0
    for export_id, attempt in rows:
        try:
            await enqueuer.enqueue(
                DELIVER_EXPORT, str(export_id), attempt, job_id=export_job_id(export_id, attempt)
            )
        except Exception:
            logger.exception("не удалось перепоставить выгрузку %s", export_id)
            continue
        requeued += 1
    return requeued


async def _claim(session: AsyncSession, export_id: UUID, seen: int) -> bool:
    """Атомарно занимает попытку: для данного seen rowcount 1 получит только один воркер."""
    res = await session.execute(
        update(Export)
        .where(Export.id == export_id, Export.status == "pending", Export.attempt == seen)
        .values(attempt=seen + 1, next_attempt_at=datetime.now(UTC) + LEASE)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return res.rowcount == 1


async def _fail(session: AsyncSession, export: Export, reason: str) -> None:
    export.status = "failed"
    export.last_error = reason[:MAX_ERROR]
    export.next_attempt_at = None
    await session.commit()


async def _skip(session: AsyncSession, export: Export, reason: str) -> None:
    """Доставка больше не нужна — не ошибка: в счётчики ошибок не попадает, «Повторить» нет."""
    export.status = "skipped"
    export.last_error = reason[:MAX_ERROR]
    export.next_attempt_at = None
    await session.commit()


def _issued(value: Any) -> date | None:
    if isinstance(value, str):
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None
    return None


def _number(value: Any) -> str | None:
    if value is None or isinstance(value, bool | list | dict):
        return None
    text = str(value).strip()
    return text or None


def cabinet_url(settings: Settings, document_id: UUID) -> str:
    return f"{settings.public_site_url.rstrip('/')}/app/documents/{document_id}"


def build_export_doc(
    document: Document,
    schema: DocSchema,
    settings: Settings,
    blobs: BlobStore,
    labels: Mapping[str, str] | None = None,
) -> ExportDoc:
    """Документ для коннектора. PDF читаются лениво: коннектор, которому они не нужны, их не грузит.

    schema — исходная; в `ExportDoc.schema` она уходит с названиями компании, исходная — в `base_schema`.
    """
    base_schema = schema
    schema = apply_labels(base_schema, labels)
    doc_id = document.id
    data = dict(document.data or {})
    status, created_at, reviewed = document.status, document.created_at, document.reviewed_at is not None
    ready = status in ("ok", "needs_review")
    # ссылки читают люди (CRM, таблица, Excel): API требует Bearer-токен, а прокси кабинета — вход по сессии;
    # без сессии кабинет отправит на вход и вернёт обратно к файлу
    site = settings.public_site_url.rstrip("/")

    async def load_scan() -> bytes | None:
        try:
            return await blobs.get(scan_key(str(doc_id)))
        except BlobNotFound:
            return None

    async def load_digital() -> bytes | None:
        try:
            return await asyncio.to_thread(
                lambda: render_digital_pdf(
                    schema, data, status=status, created_at=created_at, reviewed=reviewed,
                    font_path=settings.pdf_font_path,
                )
            )
        except Exception:
            logger.warning("цифровая версия документа %s для выгрузки не сформирована", doc_id, exc_info=True)
            return None

    return ExportDoc(
        id=doc_id,
        tenant_id=document.tenant_id,
        schema=schema,
        data=data,
        status=status,
        created_at=created_at,
        title=schema.title,
        number=_number(data.get("number")),
        issued=_issued(data.get("issued")),
        cabinet_url=cabinet_url(settings, doc_id),
        digital_pdf_url=f"{site}/app/files/{doc_id}/digital" if ready else None,
        scan_pdf_url=f"{site}/app/files/{doc_id}/pdf" if ready else None,
        issues=[dict(i) for i in document.issues or []],
        digital_loader=load_digital,
        scan_loader=load_scan,
        base_schema=base_schema,
    )


async def _check_urls(connector: Connector, config: dict, secrets: dict, *, allow_private: bool) -> None:
    """SSRF прямо перед отправкой (DNS rebinding): url-поля и настроек, и секретов. ValueError при нарушении."""
    for fields, values in ((connector.config_fields, config), (connector.secret_fields, secrets)):
        for f in fields:
            value = values.get(f.name)
            if f.type != "url" or value in (None, ""):
                continue
            if not isinstance(value, str):
                raise ValueError(f"«{f.label}»: адрес должен быть строкой")
            await asyncio.to_thread(check_webhook_url, value, allow_private=allow_private)
    check_network = getattr(connector, "check_network", None)
    if check_network is not None:
        await asyncio.to_thread(check_network, config, allow_private=allow_private)


class _Permanent(Exception):
    """Сбой, который повтор не исправит: нет ключа шифрования, нет схемы, запрещённый адрес."""


class _Undeliverable(Exception):
    """Получатель недоступен, и это не ошибка системы: выгрузка пропускается, без повторов и кнопки «Повторить»."""


async def _attempt(
    export: Export,
    dest: Destination,
    document: Document,
    connector: Connector,
    *,
    session: AsyncSession,
    http: httpx.AsyncClient,
    blobs: BlobStore,
    schemas,
    settings: Settings,
) -> str | None:
    """Сама попытка: секреты → схема → SSRF → коннектор под общим дедлайном. Возвращает external_id."""
    try:
        secrets = decrypt_secrets(dest.secrets_enc, settings.secrets_key) if dest.secrets_enc else {}
    except SecretsKeyMissing as exc:
        raise _Permanent(str(exc)) from None  # без ключа повтор не поможет
    try:
        schema = await schemas.resolve(session, document.tenant_id, document.schema_slug, document.schema_version)
    except LookupError as exc:
        raise _Permanent(str(exc)) from None
    try:
        await _check_urls(connector, dest.config or {}, secrets, allow_private=settings.webhook_allow_private)
    except UnresolvableHost as exc:
        raise ConnectorError(f"адрес отклонён: {exc}", retryable=True) from None  # DNS может ожить
    except ValueError as exc:
        # частная сеть или не та схема — ошибка настройки, повтор не поможет
        raise _Permanent(f"адрес отклонён: {exc}") from None
    ctx = DestCtx(config=dest.config or {}, secrets=secrets, mapping=dest.mapping or {}, http=http)
    labels = (await get_label_set(session, document.tenant_id, document.schema_slug)).labels
    doc = build_export_doc(document, schema, settings, blobs, labels)
    try:
        # свой дедлайн раньше job_timeout arq: иначе задачу отменят вне try и ошибка не попадёт в журнал
        async with asyncio.timeout(CONNECTOR_DEADLINE):
            if export.purpose == "export":
                return await connector.export(ctx, doc)
            await connector.notify(ctx, doc, export.purpose)
            return None
    except TimeoutError:
        raise ConnectorError(TIMED_OUT, retryable=True) from None


def _stale(export: Export, document: Document) -> str | None:
    """Почему доставка уже не нужна: документ ушёл из статуса, для которого она ставилась."""
    if export.purpose in BOT_PURPOSES and document.status in ("queued", "processing"):
        return STALE_NOTIFICATION  # распознаётся заново: о результате сообщит новый прогон
    if export.purpose == "bot_ready" and document.reviewed_at is not None and document.status != "ok":
        # отклонён: чату напишет bot_reviewed, иначе отказ выглядел бы как сбой. Одобренный PDF и ссылку получит
        return STALE_NOTIFICATION
    if export.purpose == "export" and document.status != "ok":
        # анти-цель: неподтверждённый документ не уходит в учётную систему клиента
        return NOT_APPROVED.format(status=document.status)
    if export.purpose == "notify_new" and document.status != "ok":
        return STALE_NOTIFICATION
    if export.purpose == "notify_review" and document.status != "needs_review":
        return STALE_NOTIFICATION
    return None


def _out_of_attempts(export: Export) -> str | None:
    """Попытки кончились, а результата нет (задачу убил таймаут arq или остановка воркера): больше не шлём."""
    if export.attempt <= len(RETRY_DELAYS):
        return None
    return ATTEMPTS_EXHAUSTED + (f": {export.last_error}" if export.last_error else "")


async def _finish_attempt(
    session: AsyncSession,
    export: Export,
    enqueuer: Enqueuer,
    work: Callable[[], Awaitable[str | None]],
    *,
    kind: str,
) -> tuple[bool, str | None]:
    """Общий хвост попытки: занять, выполнить work (вернёт external_id), записать итог, назначить повтор.

    Возвращает (наша ли попытка, ошибка); False — попытку занял другой воркер, work не вызывался.
    """
    if not await _claim(session, export.id, export.attempt):
        return False, None
    await session.refresh(export)

    error: str | None = None
    retryable = True
    external_id: str | None = None
    try:
        external_id = await work()
    except _Undeliverable as exc:
        await _skip(session, export, str(exc))
        return True, str(exc)
    except _Permanent as exc:
        error, retryable = str(exc), False
    except ConnectorError as exc:
        error, retryable = exc.message, exc.retryable
    except Exception as exc:
        # текст исключения может содержать адрес с ключом или токен: ни в журнал, ни в лог — только тип
        logger.warning("выгрузка %s: сбой %s (%s)", export.id, kind, type(exc).__name__)
        error = f"сбой при обращении к системе ({type(exc).__name__})"
        await session.rollback()  # транзакция могла сломаться (сбой БД); попытка уже закоммичена claim'ом
        await session.refresh(export)

    if error is None:
        export.status = "done"
        export.external_id = None if external_id is None else str(external_id)[:MAX_ERROR]
        export.last_error = None
        export.next_attempt_at = None
        await session.commit()
        return True, None

    attempt = export.attempt  # уже увеличен claim'ом
    if not retryable or attempt > len(RETRY_DELAYS):
        await _fail(session, export, error)
        return True, error
    export.last_error = error[:MAX_ERROR]
    delay = RETRY_DELAYS[attempt - 1]
    export.next_attempt_at = datetime.now(UTC) + timedelta(seconds=delay)
    await session.commit()  # до постановки в очередь
    await enqueuer.enqueue(
        DELIVER_EXPORT, str(export.id), attempt, defer_by=delay, job_id=export_job_id(export.id, attempt)
    )
    return True, error


async def _bot_attempt(
    export: Export,
    document: Document,
    bot: TenantBot,
    chat: BotChat,
    *,
    session: AsyncSession,
    http: httpx.AsyncClient,
    blobs: BlobStore,
    schemas,
    settings: Settings,
) -> None:
    """Одна попытка — один вызов Bot API. Схемы нет (`auto` или удалена) — только текст."""
    try:
        token = decrypt_secrets(bot.token_enc, settings.secrets_key)["token"]
    except SecretsKeyMissing as exc:
        raise _Permanent(str(exc)) from None
    doc: ExportDoc | None = None
    queue_fields: list[str] = []
    if document.schema_slug != AUTO_SLUG:
        try:
            schema = await schemas.resolve(session, document.tenant_id, document.schema_slug, document.schema_version)
        except LookupError:
            pass
        else:
            label_set = await get_label_set(session, document.tenant_id, document.schema_slug)
            doc = build_export_doc(document, schema, settings, blobs, label_set.labels)
            queue_fields = queue_fields_for(schema, label_set.queue_fields)
    ref = _own_source(document, bot.id)
    full = ref is not None and ref.get("chat_id") == chat.chat_id
    reply_to = ref.get("message_id") if full else None
    link = cabinet_url(settings, document.id)
    await session.commit()  # на время PDF и вызова Telegram транзакция закрыта
    api = BotApi(http, token, settings.telegram_api_url)
    try:
        async with asyncio.timeout(CONNECTOR_DEADLINE):
            await send_bot_export(
                api,
                chat.chat_id,
                reply_to,
                doc,
                document,
                export.purpose,
                full=full,
                queue_fields=queue_fields,
                cabinet_url=link,
            )
    except TimeoutError:
        raise ConnectorError(TIMED_OUT, retryable=True) from None
    except BotTokenRevoked:
        raise _Permanent(TOKEN_REVOKED) from None
    except BotChatUnavailable:
        raise _Undeliverable(chat_unavailable_text(chat)) from None
    except BotApiError as exc:
        raise ConnectorError(exc.message, retryable=exc.retryable) from None


async def _save_bot_status(session: AsyncSession, bot_id: UUID, error: str | None, chat_id: int) -> None:
    """Итог отправки в чат chat_id. Запросом с условием: чужие ошибки опроса и отзыв токена не перетираем.

    Ошибку недоступного чата не стирает ни успех в другом чате, ни его сбой: иначе владелец не узнал бы, что один из
    чатов больше не получает документы.
    """
    query = update(TenantBot).where(TenantBot.id == bot_id).execution_options(synchronize_session=False)
    about_chat = TenantBot.last_error.startswith(CHAT_ERROR_PREFIX, autoescape=True)
    if error is None:
        query = query.where(
            TenantBot.last_error.startswith(SEND_ERROR_PREFIX, autoescape=True),
            or_(~about_chat, last_error_about_chat(chat_id)),
        ).values(last_error=None)
    elif error == TOKEN_REVOKED:
        query = query.values(active=False, last_error=TOKEN_REVOKED)
    else:
        text = f"{SEND_ERROR_PREFIX}{error}"
        query = query.where(TenantBot.active.is_(True))
        if not text.startswith(CHAT_ERROR_PREFIX):
            query = query.where(or_(TenantBot.last_error.is_(None), ~about_chat))
        query = query.values(last_error=text[:MAX_ERROR])
    await session.execute(query)
    await session.commit()


async def _deliver_bot(
    session: AsyncSession,
    export: Export,
    *,
    http: httpx.AsyncClient,
    enqueuer: Enqueuer,
    blobs: BlobStore,
    schemas,
    settings: Settings,
) -> None:
    chat = await session.get(BotChat, export.bot_chat_id) if export.bot_chat_id else None
    if chat is None:
        return await _skip(session, export, BOT_CHAT_GONE)
    bot = await session.get(TenantBot, chat.bot_id)
    if bot is None or not bot.active or bot.tenant_id != export.tenant_id:
        return await _skip(session, export, BOT_OFF)
    document = await session.get(Document, export.document_id)
    if document is None:
        return await _fail(session, export, DOCUMENT_GONE)
    if reason := _stale(export, document):
        return await _skip(session, export, reason)
    if reason := _out_of_attempts(export):
        return await _fail(session, export, reason)

    # после отката сессии объекты бота и чата уже не прочитать
    bot_id, telegram_chat_id = bot.id, chat.chat_id

    async def work() -> None:
        await _bot_attempt(
            export, document, bot, chat, session=session, http=http, blobs=blobs, schemas=schemas, settings=settings
        )

    mine, error = await _finish_attempt(session, export, enqueuer, work, kind="tenant_bot")
    if mine:
        await _save_bot_status(session, bot_id, error, telegram_chat_id)


async def deliver_export(
    session: AsyncSession,
    export_id: UUID,
    *,
    http: httpx.AsyncClient,
    enqueuer: Enqueuer,
    blobs: BlobStore,
    schemas,
    settings: Settings,
    expected_attempt: int | None = None,
) -> None:
    """Одна попытка доставки. schemas — SchemaResolver; expected_attempt — номер попытки, под который ставилась
    задача (None — задачи без номера)."""
    export = await session.get(Export, export_id)
    if export is None or export.status != "pending":
        return
    if (
        expected_attempt is not None
        and export.attempt > expected_attempt
        and export.next_attempt_at is not None
        and export.next_attempt_at > datetime.now(UTC) + LEASE_SLACK
    ):
        # заблудившийся дубль: попытку уже заняла (или отложила) другая задача — второй отправки не будет
        return
    if export.purpose in BOT_PURPOSES:
        return await _deliver_bot(
            session, export, http=http, enqueuer=enqueuer, blobs=blobs, schemas=schemas, settings=settings
        )
    dest = await session.get(Destination, export.destination_id) if export.destination_id else None
    document = await session.get(Document, export.document_id)
    if dest is None or not dest.active or dest.tenant_id != export.tenant_id:
        return await _fail(session, export, DESTINATION_OFF)
    if document is None:
        return await _fail(session, export, DOCUMENT_GONE)
    if reason := _stale(export, document):
        # устаревшее уведомление — пропуск; неподтверждённый документ в учётку — ошибка, её видно и можно повторить
        return await (_skip if reason == STALE_NOTIFICATION else _fail)(session, export, reason)
    connector = REGISTRY.get(dest.kind)
    if connector is None:
        return await _fail(session, export, f"тип получателя «{dest.kind}» больше не поддерживается")
    if reason := _out_of_attempts(export):
        return await _fail(session, export, reason)

    async def work() -> str | None:
        return await _attempt(
            export, dest, document, connector,
            session=session, http=http, blobs=blobs, schemas=schemas, settings=settings,
        )

    await _finish_attempt(session, export, enqueuer, work, kind=dest.kind)
