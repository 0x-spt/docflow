"""Конвейер одного документа: страницы → PDF (+OCR) → извлечение → нормализация → проверки → статус.

Документ всегда заканчивает в ok / needs_review / failed: зависший processing хуже, чем failed.
"""

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from docflow.config import get_settings
from docflow.db.models import Document, SchemaRecord, Tenant
from docflow.exports import enqueue_routed
from docflow.extraction.base import UNKNOWN, ExtractionError, Extractor
from docflow.extraction.normalize import normalize
from docflow.imaging.load import UnsupportedFile
from docflow.imaging.prepare import prepare_pages
from docflow.pdf.build import NoOcr, OcrEngine, build_pdf
from docflow.queue import PROCESS_DOCUMENT, Enqueuer
from docflow.routing import RoutingDecision, evaluate_rules, fetch_dictionaries, load_rules
from docflow.schemas.catalog import AUTO_SLUG, list_doc_types
from docflow.schemas.library import builtin_schemas
from docflow.schemas.llm import UNCERTAIN
from docflow.schemas.model import DocSchema
from docflow.storage.blobs import BlobNotFound, BlobStore, page_key, scan_key
from docflow.validation.dictionaries import DictEntry, resolve_dictionaries
from docflow.validation.engine import Issue, decide_status, evaluate
from docflow.webhooks import enqueue_webhooks

logger = logging.getLogger(__name__)

INTERNAL_ERROR = "внутренняя ошибка обработки"
REVIEW_REQUIRED = Issue(
    None,
    "review_required",
    "модель этого клиента не подтверждена эталонным прогоном: результат — черновик, нужна проверка человеком",
)
APPROVAL_REQUIRED = "approval_required"
TIMEOUT_ERROR = "обработка прервана: превышено время или воркер остановлен"
REAPED_ERROR = "обработка прервана (воркер остановился)"
AUTODETECT_FAILED = "Не удалось определить тип документа"


class SchemaNotFound(LookupError):
    pass


class _Failure(Exception):
    """Ожидаемая причина отказа; текст уходит клиенту в document.error."""


class SchemaResolver:
    """Схема тенанта → встроенная схема в БД → встроенная библиотека (работает и без сидирования)."""

    def __init__(self, builtins: dict[str, DocSchema] | None = None) -> None:
        self._builtins = builtins

    async def resolve(self, session: AsyncSession, tenant_id: UUID | None, slug: str, version: int | None) -> DocSchema:
        owners = [tenant_id, None] if tenant_id is not None else [None]
        for owner in owners:
            q = select(SchemaRecord.body).where(
                SchemaRecord.slug == slug,
                SchemaRecord.tenant_id.is_(None) if owner is None else SchemaRecord.tenant_id == owner,
            )
            if version is not None:
                q = q.where(SchemaRecord.version == version)
            body = (await session.execute(q.order_by(SchemaRecord.version.desc()).limit(1))).scalar_one_or_none()
            if body is not None:
                return DocSchema.model_validate(body)
        if self._builtins is None:
            self._builtins = builtin_schemas()
        schema = self._builtins.get(slug)
        if schema is not None and (version is None or schema.version == version):
            return schema
        suffix = f" версии {version}" if version is not None else ""
        raise SchemaNotFound(f"Схема «{slug}»{suffix} не найдена")


@dataclass
class Deps:
    sessionmaker: async_sessionmaker
    blobs: BlobStore
    ocr: OcrEngine
    extractors: dict[str, Extractor]
    schemas: SchemaResolver
    enqueuer: Enqueuer
    max_pages: int = field(default_factory=lambda: get_settings().max_pages)


async def _claim(session: AsyncSession, document_id: UUID) -> bool:
    """queued → processing атомарно: повторная задача или второй воркер получат rowcount 0."""
    res = await session.execute(
        update(Document)
        .where(Document.id == document_id, Document.status == "queued")
        .values(status="processing", claimed_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return res.rowcount == 1


def _finish(document_id: UUID, **values):
    """UPDATE только из processing: поздний результат не перетрёт failed от жнеца и наоборот."""
    return (
        update(Document)
        .where(Document.id == document_id, Document.status == "processing")
        .values(finished_at=datetime.now(UTC), **values)
        .execution_options(synchronize_session=False)
    )


async def _scan_pdf(deps: Deps, pages) -> bytes:
    pdf = await asyncio.to_thread(build_pdf, pages, NoOcr())
    try:
        return await asyncio.to_thread(deps.ocr.add_text_layer, pdf)
    except Exception:
        # Извлечение читает картинки, а не текстовый слой: без OCR документ всё равно полезен
        logger.warning("OCR не удался, сохраняем PDF без текстового слоя", exc_info=True)
        return pdf


async def _render(deps: Deps, document_id: UUID, keys: list[str], mode: str) -> tuple[list[bytes], int]:
    """Файлы → scan.pdf в хранилище; наружу только JPEG для модели.

    Исходники, полноразмерные страницы и PDF живут только внутри этой функции: на время вызова модели
    (до минут) в памяти не держим ~1 ГБ на задачу.
    """
    files = [(await deps.blobs.get(key), key.rsplit("/", 1)[-1]) for key in keys]
    pages = await asyncio.to_thread(prepare_pages, files, mode, deps.max_pages)
    del files
    pdf = await _scan_pdf(deps, pages)
    await deps.blobs.put(scan_key(str(document_id)), pdf, "application/pdf")
    # превью для кабинета: ровно те JPEG, что видит модель
    for n, page in enumerate(pages, start=1):
        await deps.blobs.put(page_key(str(document_id), n), page.model_jpeg, "image/jpeg")
    return [p.model_jpeg for p in pages], len(pages)


async def load_dictionaries(session: AsyncSession, tenant_id: UUID, schema: DocSchema) -> dict[str, list[DictEntry]]:
    """Справочники, на которые ссылается схема. Незаведённого в ответе нет — resolve_dictionaries это отметит."""
    names = {r.params["name"] for f in schema.fields for r in f.rules if r.kind == "dictionary"}
    return await fetch_dictionaries(session, tenant_id, names)


def approval_issues(decision: RoutingDecision) -> list[Issue]:
    return [Issue(None, APPROVAL_REQUIRED, f"нужно согласование: {name}") for name in decision.approval]


async def _load_context(
    session: AsyncSession, tenant_id: UUID, schema: DocSchema
) -> tuple[dict[str, list[DictEntry]], list, dict]:
    """Справочники и правила маршрутизации для уже найденной схемы."""
    dictionaries = await load_dictionaries(session, tenant_id, schema)
    rules, rule_dictionaries = await load_rules(session, tenant_id)
    return dictionaries, rules, rule_dictionaries


async def _detect_schema(
    deps: Deps,
    session: AsyncSession,
    document_id: UUID,
    tenant_id: UUID,
    extractor: Extractor,
    first_page: bytes,
) -> DocSchema:
    """Тип документа определяет экстрактор тенанта (локальный не уходит в облако); тип пишется в документ."""
    types = await list_doc_types(session, tenant_id)
    await session.commit()  # на время вызова модели транзакция закрыта
    try:
        slug = await asyncio.to_thread(extractor.classify, types, first_page)
    except Exception as exc:
        # текст исключения не логируем: в нём может быть тело ответа API
        logger.warning("определение типа документа %s не удалось: %s", document_id, type(exc).__name__)
        raise _Failure(AUTODETECT_FAILED) from None
    chosen = next((t for t in types if t.slug == slug), None) if slug and slug != UNKNOWN else None
    if chosen is None:
        raise _Failure(AUTODETECT_FAILED)
    await session.execute(
        update(Document)
        .where(Document.id == document_id, Document.status == "processing")
        .values(schema_slug=chosen.slug, schema_version=chosen.version)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return await deps.schemas.resolve(session, tenant_id, chosen.slug, chosen.version)


async def _run(deps: Deps, session: AsyncSession, document_id: UUID) -> tuple[bool, RoutingDecision, str]:
    """(записан ли результат — документ ещё был в processing; решение маршрутизации; записанный статус)."""
    document = await session.get(Document, document_id)
    tenant = await session.get(Tenant, document.tenant_id)
    kind = tenant.extractor if tenant else None
    extractor = deps.extractors.get(kind) if kind else None
    if extractor is None:
        raise _Failure(f"Экстрактор «{kind}» не настроен")
    tenant_id, auto_ok = document.tenant_id, tenant.auto_ok
    keys, mode = list(document.files), document.mode
    autodetect = document.schema_slug == AUTO_SLUG
    if not autodetect:
        schema = await deps.schemas.resolve(session, tenant_id, document.schema_slug, document.schema_version)
        dictionaries, rules, rule_dictionaries = await _load_context(session, tenant_id, schema)
    # Закрываем читающую транзакцию: OCR и модель идут минутами, idle in transaction на Postgres не нужен
    await session.commit()

    images, n_pages = await _render(deps, document_id, keys, mode)
    if autodetect:
        schema = await _detect_schema(deps, session, document_id, tenant_id, extractor, images[0])
        dictionaries, rules, rule_dictionaries = await _load_context(session, tenant_id, schema)
        await session.commit()
    extraction = await asyncio.to_thread(extractor.extract, schema, images)
    del images
    data, issues = normalize(schema, extraction.raw)
    issues += resolve_dictionaries(schema, data, dictionaries)  # до проверок: они видят исправленные значения
    issues += evaluate(schema, data)
    data.pop(UNCERTAIN, None)  # неуверенность уже стала issues с кодом uncertain
    if not auto_ok and decide_status(issues) == "ok":
        issues.append(REVIEW_REQUIRED)
    decision = evaluate_rules(rules, schema, data, rule_dictionaries)
    if decide_status(issues) == "ok":
        # согласование — до записи статуса: и вебхук, и кабинет увидят needs_review, выгрузки не будет
        issues += approval_issues(decision)

    status = decide_status(issues)
    res = await session.execute(
        _finish(
            document_id,
            status=status,
            data=data,
            issues=[asdict(i) for i in issues],
            error=None,
            model=extraction.model,
            input_tokens=extraction.input_tokens,
            output_tokens=extraction.output_tokens,
            pages=n_pages,
        )
    )
    await session.commit()
    return res.rowcount == 1, decision, status


async def _mark_failed(session: AsyncSession, document_id: UUID, error: str) -> bool:
    await session.rollback()  # сбрасываем сломанную транзакцию, если она была
    res = await session.execute(_finish(document_id, status="failed", error=error))
    await session.commit()
    return res.rowcount == 1


async def _enqueue_webhooks(session: AsyncSession, document_id: UUID, enqueuer: Enqueuer) -> None:
    try:
        document = await session.get(Document, document_id, populate_existing=True)
        await enqueue_webhooks(session, document, enqueuer)
    except Exception:
        # Документ уже в финальном статусе; закоммиченные доставки поднимет requeue_stale
        logger.exception("не удалось поставить вебхуки документа %s", document_id)


async def _enqueue_routed(
    session: AsyncSession, document_id: UUID, decision: RoutingDecision, written: str, enqueuer: Enqueuer
) -> None:
    """Выгрузки по решению конвейера — только если документ всё ещё в записанном статусе.

    Если оператор успел проверить документ, решение устарело: одобрение ставит выгрузки само.
    """
    try:
        document = await session.get(Document, document_id, populate_existing=True)
        if document is None or document.status != written:
            logger.info("документ %s уже в другом статусе, выгрузки конвейера не ставятся", document_id)
            return
        await enqueue_routed(session, document, decision, enqueuer)
    except Exception:
        # документ уже в финальном статусе; закоммиченные выгрузки поднимет requeue_stale_exports
        logger.exception("не удалось поставить выгрузки документа %s", document_id)


async def process_document(deps: Deps, document_id: UUID) -> None:
    async with deps.sessionmaker() as session:
        if not await _claim(session, document_id):
            return
        error: str | None = None
        finished = False
        decision: RoutingDecision | None = None
        written = ""
        try:
            finished, decision, written = await _run(deps, session, document_id)
        except (UnsupportedFile, _Failure, SchemaNotFound) as exc:
            error = str(exc)
        except BlobNotFound as exc:
            error = f"Исходный файл не найден в хранилище: {exc.args[0]}"
        except ExtractionError as exc:
            error = f"Не удалось извлечь данные ({exc})"
        except asyncio.CancelledError:
            # job_timeout arq или остановка воркера: не оставляем документ в processing
            logger.warning("обработка документа %s прервана", document_id)
            if await _mark_failed(session, document_id, TIMEOUT_ERROR):
                await _enqueue_webhooks(session, document_id, deps.enqueuer)
            raise
        except Exception:
            logger.exception("ошибка обработки документа %s", document_id)
            error = INTERNAL_ERROR
        if error is not None:
            finished = await _mark_failed(session, document_id, error)
        # rowcount 0 — документ уже закрыл жнец (и поставил вебхуки сам)
        if finished:
            await _enqueue_webhooks(session, document_id, deps.enqueuer)
            if decision is not None:
                await _enqueue_routed(session, document_id, decision, written, deps.enqueuer)


async def reap_stale_processing(
    session: AsyncSession, enqueuer: Enqueuer, now: datetime, older_than: timedelta
) -> int:
    """Документы, застрявшие в processing (воркер убит после claim), переводит в failed с вебхуком."""
    cutoff = now - older_than
    stale = (
        await session.execute(
            select(Document.id).where(Document.status == "processing", Document.claimed_at < cutoff)
        )
    ).scalars().all()
    await session.commit()
    reaped = 0
    for document_id in stale:
        res = await session.execute(
            update(Document)
            .where(Document.id == document_id, Document.status == "processing", Document.claimed_at < cutoff)
            .values(status="failed", error=REAPED_ERROR, finished_at=now)
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if res.rowcount == 1:
            reaped += 1
            await _enqueue_webhooks(session, document_id, enqueuer)
    return reaped


def process_job_id(document_id: UUID | str) -> str:
    """Один job_id на документ: arq не поставит вторую копию, пока первая в очереди или выполняется."""
    return f"process:{document_id}"


async def requeue_stale_queued(
    session: AsyncSession, enqueuer: Enqueuer, now: datetime, older_than: timedelta
) -> int:
    """Документы, застрявшие в queued (задача потеряна: Redis упал/очищен, API упал между commit и enqueue),
    ставит в очередь заново. Возвращает число документов, для которых вызвана постановка.

    Повтор безопасен: с тем же job_id arq не создаст дубль, пока задача лежит в очереди или выполняется
    (существует ключ задачи), а если дубль всё же отработает — второй _claim получит rowcount 0.
    Ключ результата (он тоже блокирует постановку с тем же job_id) у process_document не пишется:
    в WorkerSettings задаче выставлен keep_result=0, иначе задача, упавшая до claim, на час (keep_result
    по умолчанию) заблокировала бы перепостановку своего документа.
    """
    cutoff = now - older_than
    stale = (
        await session.execute(
            select(Document.id).where(Document.status == "queued", Document.created_at < cutoff)
        )
    ).scalars().all()
    await session.commit()
    requeued = 0
    for document_id in stale:
        try:
            await enqueuer.enqueue(PROCESS_DOCUMENT, str(document_id), job_id=process_job_id(document_id))
        except Exception:
            # Redis недоступен — следующий проход крона попробует снова
            logger.exception("не удалось перепоставить документ %s", document_id)
            continue
        requeued += 1
    return requeued
