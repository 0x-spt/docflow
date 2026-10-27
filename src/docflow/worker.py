"""Воркер arq: обработка документов, доставка вебхуков, подбор потерянных доставок.

Запуск: `arq docflow.worker.WorkerSettings`.
"""

import json
import logging
from datetime import UTC, datetime, timedelta
from uuid import UUID

import httpx
from arq import cron
from arq.connections import RedisSettings
from arq.worker import func

from docflow.config import Settings, get_settings
from docflow.db.session import make_engine, make_sessionmaker
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
from docflow.queue import DELIVER_WEBHOOK, PROCESS_DOCUMENT, ArqEnqueuer
from docflow.storage.blobs import blob_store_from_settings
from docflow.webhooks import deliver, requeue_stale

logger = logging.getLogger(__name__)

# Бюджет времени связан тремя числами:
#   JOB_TIMEOUT (300 с) — arq отменяет задачу, process_document пишет failed (TIMEOUT_ERROR);
#   таймауты экстракторов (Claude 240 с + 1 повтор на быстрые отказы, Ollama 240 с) — ответ модели вместе
#   с подготовкой страниц и OCR должен уложиться в JOB_TIMEOUT, иначе задачу убьёт arq;
#   STALE_PROCESSING = JOB_TIMEOUT + 5 мин — после этого processing считается брошенным (воркер убит
#   без шанса записать failed) и его закрывает жнец.
# Меняя одно, проверьте остальные.
JOB_TIMEOUT = 300
# processing дольше job_timeout + запас — воркер точно мёртв (живой бы уже записал результат или failed)
STALE_PROCESSING = timedelta(seconds=JOB_TIMEOUT) + timedelta(minutes=5)
# queued дольше этого — задача, скорее всего, потеряна; перепостановка с тем же job_id безопасна и при живой задаче
STALE_QUEUED = timedelta(minutes=5)


def _extractors(settings: Settings) -> dict[str, Extractor]:
    if settings.fake_extractor:
        responses = json.loads(settings.fake_responses.read_text(encoding="utf-8"))
        fake = FakeExtractor(responses)
        return {"claude": fake, "ollama": fake}
    # ClaudeExtractor не требует ANTHROPIC_API_KEY при создании — только при запросе
    return {
        "claude": ClaudeExtractor(model=settings.anthropic_model),
        "ollama": OllamaExtractor(settings.ollama_url, settings.ollama_model),
    }


async def startup(ctx: dict) -> None:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    ctx["settings"] = settings
    ctx["engine"] = engine
    ctx["http"] = httpx.AsyncClient()
    ctx["deps"] = Deps(
        sessionmaker=make_sessionmaker(engine),
        blobs=blob_store_from_settings(settings),
        ocr=OcrMyPdf(),
        extractors=_extractors(settings),
        schemas=SchemaResolver(),
        enqueuer=ArqEnqueuer(ctx["redis"]),
        max_pages=settings.max_pages,
    )
    if settings.fake_extractor:
        logger.warning("DOCFLOW_FAKE_EXTRACTOR включён: ответы берутся из %s", settings.fake_responses)


async def shutdown(ctx: dict) -> None:
    http = ctx.get("http")
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
    return {"reaped_documents": reaped, "requeued_documents": queued, "requeued_deliveries": requeued}


class WorkerSettings:
    # имена задач — те же, что ставит Enqueuer (PROCESS_DOCUMENT, DELIVER_WEBHOOK), а не имена функций
    functions = [
        # результат не храним: статус документа живёт в БД, а ключ результата arq блокировал бы
        # перепостановку с тем же job_id (см. requeue_stale_queued)
        func(process_document_task, name=PROCESS_DOCUMENT, keep_result=0),
        func(deliver_webhook_task, name=DELIVER_WEBHOOK),
    ]
    cron_jobs = [cron(requeue_stale_task, minute=set(range(0, 60, 5)))]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = 4
    # ключ здоровья в Redis обновляется раз в минуту: на нём держится `arq --check` (healthcheck контейнера)
    health_check_interval = 60
    job_timeout = JOB_TIMEOUT
