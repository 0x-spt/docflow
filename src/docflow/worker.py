"""Воркер arq: обработка документов, доставка вебхуков и выгрузок, подбор потерянных доставок.

Запуск: `arq docflow.worker.WorkerSettings`.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from arq import cron
from arq.connections import RedisSettings
from arq.worker import func

from docflow.config import Settings, get_settings
from docflow.db.session import make_engine, make_sessionmaker
from docflow.exports import deliver_export, requeue_stale_exports
from docflow.extraction.base import Extractor
from docflow.extraction.claude import ClaudeExtractor
from docflow.extraction.fake import FakeExtractor
from docflow.extraction.ollama import OllamaExtractor
from docflow.pdf.build import OcrMyPdf
from docflow.pipeline import (
    Deps,
    SchemaResolver,
    process_document,
    reap_stale_processing,
    requeue_stale_queued,
)
from docflow.queue import DELIVER_EXPORT, DELIVER_WEBHOOK, PROCESS_DOCUMENT, ArqEnqueuer
from docflow.storage.blobs import blob_store_from_settings
from docflow.webhooks import deliver, requeue_stale

logger = logging.getLogger(__name__)

# Бюджет времени связан тремя числами:
#   JOB_TIMEOUT (480 с = 240 на извлечение + до 180 на определение типа + подготовка страниц и OCR) —
#   arq отменяет задачу, process_document пишет failed (TIMEOUT_ERROR);
#   таймауты экстракторов (Claude 240 с + 1 повтор на быстрые отказы, Ollama 240 с;
#   определение типа 30 с Claude / 180 с Ollama) —
#   ответ модели вместе с подготовкой страниц и OCR должен уложиться в JOB_TIMEOUT, иначе задачу убьёт arq;
#   STALE_PROCESSING = JOB_TIMEOUT + 5 мин — после этого processing считается брошенным (воркер убит
#   без шанса записать failed) и его закрывает жнец.
# Меняя одно, проверьте остальные.
JOB_TIMEOUT = 480
# processing дольше job_timeout + запас — воркер точно мёртв (живой бы уже записал результат или failed)
STALE_PROCESSING = timedelta(seconds=JOB_TIMEOUT) + timedelta(minutes=5)
# queued дольше этого — задача, скорее всего, потеряна; перепостановка с тем же job_id безопасна и при живой задаче
STALE_QUEUED = timedelta(minutes=5)
# таймаут каждого запроса коннектора к системе клиента
CONNECTOR_TIMEOUT = 20.0


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


class FakeExtractorNotAllowed(RuntimeError):
    pass


def site_url_warning(settings: Settings) -> str | None:
    """Прод (частные адреса запрещены) с адресом кабинета по умолчанию: ссылки в CRM, таблицах и уведомлениях
    вели бы на localhost клиента."""
    if settings.webhook_allow_private:
        return None
    host = (urlsplit(settings.public_site_url).hostname or "").lower()
    if host in LOCAL_HOSTS or host.endswith(".localhost"):
        return (
            f"DOCFLOW_PUBLIC_SITE_URL={settings.public_site_url} — локальный адрес: ссылки на документы в "
            "выгрузках и уведомлениях не откроются у клиента. Укажите внешний адрес кабинета."
        )
    return None


def _extractors(settings: Settings) -> dict[str, Extractor]:
    if settings.fake_extractor:
        if not settings.allow_fake:
            # заготовки вместо модели — тихая порча данных клиентов; без явного второго флага не стартуем
            raise FakeExtractorNotAllowed(
                "DOCFLOW_FAKE_EXTRACTOR=1 без DOCFLOW_ALLOW_FAKE=1: fake-экстрактор только для демо и e2e"
            )
        responses = json.loads(settings.fake_responses.read_text(encoding="utf-8"))
        fake = FakeExtractor(responses)
        return {"claude": fake, "ollama": fake}
    # ClaudeExtractor не требует ANTHROPIC_API_KEY при создании — только при запросе
    return {
        "claude": ClaudeExtractor(model=settings.anthropic_model, classify_model=settings.anthropic_classify_model),
        "ollama": OllamaExtractor(settings.ollama_url, settings.ollama_model),
    }


async def startup(ctx: dict) -> None:
    logging.getLogger("httpx").setLevel(logging.WARNING)  # URL с токеном бота не должны попадать в INFO-логи
    settings = get_settings()
    # экстракторы — первыми: запрет fake-режима должен сработать до открытия соединений
    extractors = _extractors(settings)
    engine = make_engine(settings.database_url)
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["http"] = httpx.AsyncClient()
    ctx["deps"] = Deps(
        sessionmaker=make_sessionmaker(engine),
        blobs=blob_store_from_settings(settings),
        ocr=OcrMyPdf(),
        extractors=extractors,
        schemas=SchemaResolver(),
        enqueuer=ArqEnqueuer(ctx["redis"]),
        max_pages=settings.max_pages,
    )
    if settings.fake_extractor:
        logger.warning("DOCFLOW_FAKE_EXTRACTOR включён: ответы берутся из %s", settings.fake_responses)
    if warning := site_url_warning(settings):
        logger.warning(warning)


async def shutdown(ctx: dict) -> None:
    for name in ("http", "connector_http"):
        http = ctx.get(name)
        if http is not None:
            await http.aclose()
    engine = ctx.get("engine")
    if engine is not None:
        await engine.dispose()


async def process_document_task(ctx: dict, document_id: str) -> None:
    await process_document(ctx["deps"], UUID(str(document_id)))


async def deliver_webhook_task(ctx: dict, delivery_id: str) -> None:
    http = ctx.get("http")
    if http is None or http.is_closed:
        http = ctx["http"] = httpx.AsyncClient()
    deps: Deps = ctx["deps"]
    settings = ctx.get("settings") or get_settings()
    async with deps.sessionmaker() as session:
        await deliver(
            session, UUID(str(delivery_id)), http, deps.enqueuer, allow_private=settings.webhook_allow_private
        )


async def deliver_export_task(ctx: dict, export_id: str, attempt: int | None = None) -> None:
    http = ctx.get("connector_http")
    if http is None or http.is_closed:
        # без редиректов (httpx по умолчанию): адрес клиента проверен на SSRF, переход на другой — нет
        http = ctx["connector_http"] = httpx.AsyncClient(timeout=CONNECTOR_TIMEOUT)
    deps: Deps = ctx["deps"]
    settings = ctx.get("settings") or get_settings()
    async with deps.sessionmaker() as session:
        await deliver_export(
            session,
            UUID(str(export_id)),
            http=http,
            enqueuer=deps.enqueuer,
            blobs=deps.blobs,
            schemas=deps.schemas,
            settings=settings,
            expected_attempt=attempt,
        )


async def requeue_stale_task(ctx: dict) -> dict[str, int]:
    """Раз в 5 минут: закрыть документы убитых воркеров, вернуть в очередь потерянные документы и доставки."""
    deps: Deps = ctx["deps"]
    now = datetime.now(UTC)
    async with deps.sessionmaker() as session:
        reaped = await reap_stale_processing(session, deps.enqueuer, now, STALE_PROCESSING)
    async with deps.sessionmaker() as session:
        queued = await requeue_stale_queued(session, deps.enqueuer, now, STALE_QUEUED)
    async with deps.sessionmaker() as session:
        requeued = await requeue_stale(session, deps.enqueuer, now)
    async with deps.sessionmaker() as session:
        exports = await requeue_stale_exports(session, deps.enqueuer, now)
    return {
        "reaped_documents": reaped,
        "requeued_documents": queued,
        "requeued_deliveries": requeued,
        "requeued_exports": exports,
    }


class WorkerSettings:
    # имена задач — те же, что ставит Enqueuer (PROCESS_DOCUMENT, DELIVER_WEBHOOK, DELIVER_EXPORT), а не имена функций
    functions = [
        # результат не храним: статус документа живёт в БД, а ключ результата arq блокировал бы
        # перепостановку с тем же job_id (см. requeue_stale_queued)
        func(process_document_task, name=PROCESS_DOCUMENT, keep_result=0),
        func(deliver_webhook_task, name=DELIVER_WEBHOOK),
        # без ключа результата: иначе «Повторить» (тот же job_id export:<id>:0) час молча не ставилось бы
        func(deliver_export_task, name=DELIVER_EXPORT, keep_result=0),
    ]
    cron_jobs = [cron(requeue_stale_task, minute=set(range(0, 60, 5)))]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    # ключ здоровья в Redis обновляется раз в минуту: на нём держится `arq --check` (healthcheck контейнера)
    health_check_interval = 60
    job_timeout = JOB_TIMEOUT
