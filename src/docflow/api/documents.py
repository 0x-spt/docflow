"""Загрузка документов, статус и готовый PDF."""

import asyncio
import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, File, Form, Header, HTTPException, Response, UploadFile, status
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.api.deps import BlobsDep, EnqueuerDep, ResolverDep, SessionDep, SettingsDep, TenantDep, parse_id
from docflow.api.limits import request_limit_bytes
from docflow.db.models import Document
from docflow.imaging.load import UnsupportedFile, count_pages, detect_kind
from docflow.queue import PROCESS_DOCUMENT
from docflow.serializers import document_payload
from docflow.storage.blobs import BlobNotFound, original_key, scan_key

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/documents", tags=["documents"])

MODES = ("color", "gray", "bw")
CHUNK = 1024 * 1024
NOT_FOUND = "документ не найден"
ENQUEUE_ERROR = "не удалось поставить документ в очередь, загрузите его заново"
IDEMPOTENCY_KEY_MAX = 255

# сигнатура → MIME для хранилища; заголовку клиента не верим
_IMAGE_TYPES = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG", "image/png"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def _request_limit(settings) -> int:
    return request_limit_bytes(settings)


def _content_type(data: bytes, kind: str) -> str:
    if kind == "pdf":
        return "application/pdf"
    for magic, mime in _IMAGE_TYPES:
        if data.startswith(magic):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/heic"  # остальное detect_kind признаёт только по ftyp HEIF


def _accepted(document: Document) -> dict:
    return {"id": str(document.id), "status": document.status}


async def _find_by_idempotency_key(session: AsyncSession, tenant_id: uuid.UUID, key: str) -> Document | None:
    return (
        await session.execute(
            select(Document).where(Document.tenant_id == tenant_id, Document.idempotency_key == key)
        )
    ).scalar_one_or_none()


async def _read_capped(upload: UploadFile, limit: int, max_mb: int) -> bytes:
    """Читает файл кусками и бросает 413, как только набралось больше лимита: 2 ГБ в память не попадут."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(CHUNK):
        size += len(chunk)
        if size > limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"Файл «{upload.filename}» больше {max_mb} МБ",
            )
        chunks.append(chunk)
    return b"".join(chunks)


async def _get_own(session: AsyncSession, tenant_id: uuid.UUID, raw_id: str) -> Document:
    document = await session.get(Document, parse_id(raw_id))
    # чужой документ неотличим от несуществующего: id не должны подтверждаться
    if document is None or document.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return document


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Загрузить документ",
    description=(
        "Принимает один или несколько файлов (JPEG, PNG, WEBP, TIFF, HEIC, PDF) одного документа и ставит его "
        "в очередь. Возвращает id и статус `queued`; результат — через `GET /v1/documents/{id}` или вебхук. "
        "Повтор с тем же заголовком `Idempotency-Key` (до 255 символов) вернёт 200 и исходный документ без "
        "повторной обработки — даже если файлы в повторе другие: они не читаются и не проверяются. "
        "Ошибки: 413 — файл или весь запрос больше лимита, 415 — неподдерживаемый или битый файл, "
        "422 — неизвестная схема, неверный режим, нет файлов, слишком много файлов или страниц."
    ),
    responses={200: {"description": "Документ с этим Idempotency-Key уже был принят"}},
)
async def upload_document(
    tenant_id: TenantDep,
    session: SessionDep,
    settings: SettingsDep,
    blobs: BlobsDep,
    enqueuer: EnqueuerDep,
    resolver: ResolverDep,
    schema_slug: Annotated[str, Form(alias="schema", description="slug схемы документа, например invoice_ru")],
    files: Annotated[list[UploadFile] | None, File(description="Файлы документа (страницы по порядку)")] = None,
    schema_version: Annotated[int | None, Form(description="Версия схемы; по умолчанию последняя")] = None,
    mode: Annotated[str, Form(description="Режим скана: color, gray или bw")] = "gray",
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=IDEMPOTENCY_KEY_MAX)] = None,
):
    try:
        schema = await resolver.resolve(session, tenant_id, schema_slug, schema_version)
    except LookupError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    if mode not in MODES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "mode должен быть одним из: color, gray, bw")

    if idempotency_key is not None:
        existing = await _find_by_idempotency_key(session, tenant_id, idempotency_key)
        if existing is not None:
            return JSONResponse(_accepted(existing), status_code=status.HTTP_200_OK)

    files = files or []
    # в каждом файле минимум страница: лишние файлы отсекаем, не читая ни одного
    if len(files) > settings.max_pages:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Слишком много файлов: больше {settings.max_pages} на документ",
        )
    limit = settings.max_file_mb * 1024 * 1024
    total_limit = _request_limit(settings)
    contents: list[tuple[str, bytes, str]] = []
    total_pages = 0
    total_bytes = 0
    for upload in files:
        filename = upload.filename or "file"
        data = await _read_capped(upload, limit, settings.max_file_mb)
        total_bytes += len(data)
        if total_bytes > total_limit:
            raise HTTPException(
                status.HTTP_413_CONTENT_TOO_LARGE,
                f"Файлы документа вместе больше {settings.max_request_mb} МБ",
            )
        kind = detect_kind(data)
        if kind is None:
            raise HTTPException(
                status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                f"Файл «{filename}»: неподдерживаемый формат (нужны JPEG, PNG, WEBP, TIFF, HEIC или PDF)",
            )
        try:
            total_pages += await asyncio.to_thread(count_pages, data, filename)
        except UnsupportedFile as exc:
            raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from None
        if total_pages > settings.max_pages:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"Слишком много страниц: больше {settings.max_pages} на документ",
            )
        contents.append((filename, data, _content_type(data, kind)))
    if not contents:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Нужен хотя бы один файл в поле files")

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
    )
    session.add(document)
    try:
        # flush до записи файлов: гонку по Idempotency-Key ловим раньше, чем насорим в хранилище
        await session.flush()
    except IntegrityError:
        await session.rollback()
        winner = await _find_by_idempotency_key(session, tenant_id, idempotency_key) if idempotency_key else None
        if winner is None:
            raise
        return JSONResponse(_accepted(winner), status_code=status.HTTP_200_OK)

    try:
        for key, (_, data, content_type) in zip(keys, contents, strict=True):
            await blobs.put(key, data, content_type)
    except Exception:
        # транзакция не закоммичена: queued-документа без файлов не останется
        await session.rollback()
        logger.exception("не удалось сохранить файлы документа %s", doc_id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, "Не удалось сохранить файлы") from None
    await session.commit()

    try:
        await enqueuer.enqueue(PROCESS_DOCUMENT, str(doc_id), job_id=f"process:{doc_id}")
    except Exception:
        # без задачи queued-документ завис бы навсегда; failed честнее, клиент повторит загрузку
        logger.exception("не удалось поставить документ %s в очередь", doc_id)
        document.status = "failed"
        document.error = ENQUEUE_ERROR
        # освобождаем ключ: повтор клиента с тем же Idempotency-Key должен создать документ заново
        document.idempotency_key = None
        await session.commit()
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ENQUEUE_ERROR) from None
    return _accepted(document)


@router.get(
    "/{document_id}",
    summary="Статус и результат документа",
    description=(
        "Статус (`queued`, `processing`, `ok`, `needs_review`, `failed`), извлечённые данные, замечания проверок "
        "и ссылка на PDF, когда он готов. Тело совпадает с телом вебхука. Чужой или несуществующий документ — 404."
    ),
)
async def get_document(document_id: str, tenant_id: TenantDep, session: SessionDep, settings: SettingsDep) -> dict:
    document = await _get_own(session, tenant_id, document_id)
    return document_payload(document, settings.public_base_url)


@router.get(
    "/{document_id}/pdf",
    summary="Скан документа в PDF",
    description=(
        "PDF с текстовым слоем. 409 — документ ещё в очереди или обрабатывается, либо обработка упала "
        "до сборки PDF; 404 — документа нет или файл PDF потерян."
    ),
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "PDF документа"}},
)
async def get_document_pdf(document_id: str, tenant_id: TenantDep, session: SessionDep, blobs: BlobsDep) -> Response:
    document = await _get_own(session, tenant_id, document_id)
    if document.status in ("queued", "processing"):
        raise HTTPException(status.HTTP_409_CONFLICT, "PDF ещё не готов: документ обрабатывается")
    try:
        pdf = await blobs.get(scan_key(str(document.id)))
    except BlobNotFound:
        if document.status == "failed":
            raise HTTPException(status.HTTP_409_CONFLICT, "PDF нет: обработка документа завершилась ошибкой") from None
        raise HTTPException(status.HTTP_404_NOT_FOUND, "PDF документа не найден") from None
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{document.id}.pdf"'},
    )
