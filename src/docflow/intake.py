"""Приём документа: проверки, запись файлов, постановка в очередь. Общий для API, Telegram и почты."""

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.config import Settings
from docflow.db.models import Document
from docflow.imaging.load import UnsupportedFile, count_pages, detect_kind
from docflow.pipeline import SchemaResolver, process_job_id
from docflow.queue import PROCESS_DOCUMENT, Enqueuer
from docflow.storage.blobs import BlobStore, original_key

logger = logging.getLogger(__name__)

MODES = ("color", "gray", "bw")
ENQUEUE_ERROR = "не удалось поставить документ в очередь, загрузите его заново"

_IMAGE_TYPES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG", "image/png"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


class IntakeError(Exception):
    """Отказ в приёме; message — текст для человека."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class IntakeInvalid(IntakeError): ...  # API 422


class IntakeTooLarge(IntakeError): ...  # 413


class IntakeUnsupported(IntakeError): ...  # 415


class IntakeLimitExceeded(IntakeError): ...  # 402


class IntakeStorageFailed(IntakeError): ...  # 500


class IntakeEnqueueFailed(IntakeError): ...  # 503


@dataclass(frozen=True)
class IntakeFile:
    name: str
    # read(лимит в байтах): лениво, чтобы повтор по ключу не качал файл заново
    read: Callable[[int], Awaitable[bytes]]

    @classmethod
    def of(cls, name: str, data: bytes) -> "IntakeFile":
        async def read(limit: int) -> bytes:
            if len(data) > limit:
                raise IntakeTooLarge(f"Файл «{name}» больше {limit // (1024 * 1024)} МБ")
            return data

        return cls(name, read)


@dataclass(frozen=True)
class IntakeDeps:
    settings: Settings
    blobs: BlobStore
    enqueuer: Enqueuer
    resolver: SchemaResolver


@dataclass(frozen=True)
class IntakeResult:
    document: Document
    created: bool


def request_limit_bytes(settings) -> int:
    return settings.max_request_mb * 1024 * 1024


def _content_type(data: bytes, kind: str) -> str:
    if kind == "pdf":
        return "application/pdf"
    for magic, mime in _IMAGE_TYPES:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/heic"  # остальное detect_kind признаёт только по ftyp HEIF


async def find_by_idempotency_key(session: AsyncSession, tenant_id: uuid.UUID, key: str) -> Document | None:
    return (
        await session.execute(
            select(Document).where(Document.tenant_id == tenant_id, Document.idempotency_key == key)
        )
    ).scalar_one_or_none()


async def accept_document(
    session: AsyncSession,
    deps: IntakeDeps,
    *,
    tenant_id: uuid.UUID,
    schema_slug: str,
    files: Sequence[IntakeFile],
    schema_version: int | None = None,
    mode: str = "gray",
    idempotency_key: str | None = None,
    source: str = "api",
    source_ref: dict | None = None,
) -> IntakeResult:
    settings = deps.settings
    try:
        schema = await deps.resolver.resolve(session, tenant_id, schema_slug, schema_version)
    except LookupError as exc:
        raise IntakeInvalid(str(exc)) from None
    if mode not in MODES:
        raise IntakeInvalid("mode должен быть одним из: color, gray, bw")

    if idempotency_key is not None:
        existing = await find_by_idempotency_key(session, tenant_id, idempotency_key)
        if existing is not None:
            return IntakeResult(existing, created=False)

    # в каждом файле минимум страница: лишние файлы отсекаем, не читая ни одного
    if len(files) > settings.max_pages:
        raise IntakeInvalid(f"Слишком много файлов: больше {settings.max_pages} на документ")
    limit = settings.max_file_mb * 1024 * 1024
    total_limit = request_limit_bytes(settings)
    contents: list[tuple[str, bytes, str]] = []
    total_pages = 0
    total_bytes = 0
    for file in files:
        filename = file.name or "file"
        data = await file.read(limit)
        total_bytes += len(data)
        if total_bytes > total_limit:
            raise IntakeTooLarge(f"Файлы документа вместе больше {settings.max_request_mb} МБ")
        kind = detect_kind(data)
        if kind is None:
            raise IntakeUnsupported(
                f"Файл «{filename}»: неподдерживаемый формат (нужны JPEG, PNG, WEBP, TIFF, HEIC или PDF)"
            )
        try:
            total_pages += await asyncio.to_thread(count_pages, data, filename)
        except UnsupportedFile as exc:
            raise IntakeUnsupported(str(exc)) from None
        if total_pages > settings.max_pages:
            raise IntakeInvalid(f"Слишком много страниц: больше {settings.max_pages} на документ")
        contents.append((filename, data, _content_type(data, kind)))
    if not contents:
        raise IntakeInvalid("Нужен хотя бы один файл в поле files")

    doc_id = uuid.uuid4()
    keys = [original_key(str(doc_id), n, name) for n, (name, _, _) in enumerate(contents, start=1)]
    document = Document(
        id=doc_id,
        tenant_id=tenant_id,
        schema_slug=schema.slug,
        schema_version=schema.version,
        status="queued",
        idempotency_key=idempotency_key,
        files=keys,
        mode=mode,
        pages=total_pages,
        source=source,
        source_ref=source_ref,
    )
    session.add(document)
    try:
        # flush до записи файлов: гонку по Idempotency-Key ловим раньше, чем насорим в хранилище
        await session.flush()
    except IntegrityError:
        await session.rollback()
        winner = await find_by_idempotency_key(session, tenant_id, idempotency_key) if idempotency_key else None
        if winner is None:
            raise
        return IntakeResult(winner, created=False)

    try:
        for key, (_, data, content_type) in zip(keys, contents, strict=True):
            await deps.blobs.put(key, data, content_type)
    except Exception:
        # транзакция не закоммичена: queued-документа без файлов не останется
        await session.rollback()
        logger.exception("не удалось сохранить файлы документа %s", doc_id)
        raise IntakeStorageFailed("Не удалось сохранить файлы") from None
    await session.commit()

    try:
        await deps.enqueuer.enqueue(PROCESS_DOCUMENT, str(doc_id), job_id=process_job_id(doc_id))
    except Exception:
        # без задачи queued-документ ждал бы крона перепостановки; failed сразу честнее — клиент повторит загрузку
        logger.exception("не удалось поставить документ %s в очередь", doc_id)
        # Ошибка enqueue не значит, что задачи нет: Redis мог принять её и оборвать ответ, а воркер — уже
        # забрать документ. Поэтому failed только из queued, условным UPDATE, а не перезаписью статуса.
        res = await session.execute(
            update(Document)
            .where(Document.id == doc_id, Document.status == "queued")
            # освобождаем ключ: повтор клиента с тем же Idempotency-Key должен создать документ заново
            .values(status="failed", error=ENQUEUE_ERROR, idempotency_key=None, finished_at=datetime.now(UTC))
            .execution_options(synchronize_session=False)
        )
        await session.commit()
        if res.rowcount == 1:
            raise IntakeEnqueueFailed(ENQUEUE_ERROR) from None
        logger.info("документ %s уже забран воркером, ошибка постановки не влияет", doc_id)
    return IntakeResult(document, created=True)
