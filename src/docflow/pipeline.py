"""Конвейер одного документа: страницы → PDF (+OCR) → извлечение → нормализация → проверки → статус.

Документ всегда заканчивает в ok / needs_review / failed: зависший processing хуже, чем failed.
"""

import asyncio
import logging
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from docflow.config import get_settings
from docflow.db.models import Document, SchemaRecord, Tenant
from docflow.extraction.base import ExtractionError, Extractor
from docflow.extraction.normalize import normalize
from docflow.imaging.load import UnsupportedFile
from docflow.imaging.prepare import prepare_pages
from docflow.pdf.build import NoOcr, OcrEngine, build_pdf
from docflow.queue import Enqueuer
from docflow.schemas.library import builtin_schemas
from docflow.schemas.llm import UNCERTAIN
from docflow.schemas.model import DocSchema
from docflow.storage.blobs import BlobNotFound, BlobStore, scan_key
from docflow.validation.engine import decide_status, evaluate
from docflow.webhooks import enqueue_webhooks

logger = logging.getLogger(__name__)

INTERNAL_ERROR = "внутренняя ошибка обработки"
TIMEOUT_ERROR = "обработка прервана: превышено время или воркер остановлен"


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
        .values(status="processing")
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return res.rowcount == 1


async def _scan_pdf(deps: Deps, pages) -> bytes:
    pdf = await asyncio.to_thread(build_pdf, pages, NoOcr())
    try:
        return await asyncio.to_thread(deps.ocr.add_text_layer, pdf)
    except Exception:
        # Извлечение читает картинки, а не текстовый слой: без OCR документ всё равно полезен
        logger.warning("OCR не удался, сохраняем PDF без текстового слоя", exc_info=True)
        return pdf


async def _run(deps: Deps, session: AsyncSession, document: Document) -> None:
    tenant = await session.get(Tenant, document.tenant_id)
    kind = tenant.extractor if tenant else None
    extractor = deps.extractors.get(kind) if kind else None
    if extractor is None:
        raise _Failure(f"Экстрактор «{kind}» не настроен")
    schema = await deps.schemas.resolve(session, document.tenant_id, document.schema_slug, document.schema_version)

    files = [(await deps.blobs.get(key), key.rsplit("/", 1)[-1]) for key in document.files]
    pages = await asyncio.to_thread(prepare_pages, files, document.mode, deps.max_pages)
    pdf = await _scan_pdf(deps, pages)
    await deps.blobs.put(scan_key(str(document.id)), pdf, "application/pdf")

    extraction = await asyncio.to_thread(extractor.extract, schema, [p.model_jpeg for p in pages])
    data, issues = normalize(schema, extraction.raw)
    issues += evaluate(schema, data)
    data.pop(UNCERTAIN, None)  # неуверенность уже стала issues с кодом uncertain

    document.status = decide_status(issues)
    document.data = data
    document.issues = [asdict(i) for i in issues]
    document.error = None
    document.model = extraction.model
    document.input_tokens = extraction.input_tokens
    document.output_tokens = extraction.output_tokens
    document.pages = len(pages)
    document.finished_at = datetime.now(UTC)
    await session.commit()


async def _mark_failed(session: AsyncSession, document_id: UUID, error: str) -> Document:
    await session.rollback()  # сбрасываем недописанные поля и сломанную транзакцию
    await session.execute(
        update(Document)
        .where(Document.id == document_id)
        .values(status="failed", error=error, finished_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    return await session.get(Document, document_id, populate_existing=True)


async def _enqueue_webhooks(deps: Deps, session: AsyncSession, document: Document) -> None:
    try:
        await enqueue_webhooks(session, document, deps.enqueuer)
    except Exception:
        # Документ уже в финальном статусе; закоммиченные доставки поднимет requeue_stale
        logger.exception("не удалось поставить вебхуки документа %s", document.id)


async def process_document(deps: Deps, document_id: UUID) -> None:
    async with deps.sessionmaker() as session:
        if not await _claim(session, document_id):
            return
        document = await session.get(Document, document_id)
        error: str | None = None
        try:
            await _run(deps, session, document)
        except (UnsupportedFile, _Failure, SchemaNotFound) as exc:
            error = str(exc)
        except BlobNotFound as exc:
            error = f"Исходный файл не найден в хранилище: {exc.args[0]}"
        except ExtractionError as exc:
            error = f"Не удалось извлечь данные ({exc})"
        except asyncio.CancelledError:
            # job_timeout arq или остановка воркера: не оставляем документ в processing
            logger.warning("обработка документа %s прервана", document_id)
            document = await _mark_failed(session, document_id, TIMEOUT_ERROR)
            await _enqueue_webhooks(deps, session, document)
            raise
        except Exception:
            logger.exception("ошибка обработки документа %s", document_id)
            error = INTERNAL_ERROR
        if error is not None:
            document = await _mark_failed(session, document_id, error)
        await _enqueue_webhooks(deps, session, document)
