"""Загрузка документов, статус и готовый PDF."""

import asyncio
import logging
import re
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.api.deps import (
    BlobsDep,
    EnqueuerDep,
    PrincipalDep,
    ResolverDep,
    SessionDep,
    SettingsDep,
    TenantDep,
    parse_id,
)
from docflow.api.limits import request_limit_bytes
from docflow.db.models import Document
from docflow.imaging.load import UnsupportedFile, count_pages, detect_kind
from docflow.labels import LabelSet, apply_labels, get_label_sets, queue_fields_for, resolve_labeled
from docflow.pdf.digital import render_digital_pdf
from docflow.pipeline import SchemaNotFound, process_job_id
from docflow.queue import PROCESS_DOCUMENT
from docflow.review import ReviewConflict, review_document, route_approved
from docflow.schemas.model import DocSchema
from docflow.serializers import document_exports, document_payload
from docflow.storage.blobs import BlobNotFound, original_key, page_key, scan_key
from docflow.validation.engine import INFO_CODES
from docflow.webhooks import EVENT_REVIEWED, enqueue_webhooks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/documents", tags=["documents"])

MODES = ("color", "gray", "bw")
DocStatus = Literal["queued", "processing", "ok", "needs_review", "failed"]
SUMMARY_FIELDS = 3
CHUNK = 1024 * 1024
NOT_FOUND = "документ не найден"
ENQUEUE_ERROR = "не удалось поставить документ в очередь, загрузите его заново"
IDEMPOTENCY_KEY_MAX = 255
REVIEW_CONFLICT = "документ уже проверен или ещё обрабатывается"
REVIEW_NOTE_MAX = 1000

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
        await enqueuer.enqueue(PROCESS_DOCUMENT, str(doc_id), job_id=process_job_id(doc_id))
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
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, ENQUEUE_ERROR) from None
        logger.info("документ %s уже забран воркером, ошибка постановки не влияет", doc_id)
    return _accepted(document)


def _scalar(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return False
    return not (isinstance(value, str) and not value.strip())


def _list_item(document: Document, schema: DocSchema | None, label_set: LabelSet) -> dict:
    data = document.data or {}
    number = data.get("number")
    has_number = isinstance(number, str | int | float) and not isinstance(number, bool) and str(number).strip()
    title = str(number) if has_number else str(document.id)[:8]
    summary = []
    if schema is not None:
        # поля и подписи выбрала компания (или умолчание схемы); пустые значения пропускаем
        titles = {f.name: f.title or f.name for f in apply_labels(schema, label_set.labels).fields}
        for field in queue_fields_for(schema, label_set.queue_fields):
            if _scalar(data.get(field)):
                summary.append({"field": field, "label": titles.get(field, field), "value": data[field]})
    else:
        # схемы уже нет (удалена): первые поля данных как есть
        for field, value in data.items():
            if field == "number" or not _scalar(value):
                continue
            summary.append({"field": field, "label": field, "value": value})
            if len(summary) == SUMMARY_FIELDS:
                break
    issue = None
    if document.status in ("needs_review", "failed"):
        issue = next((i.get("message") for i in document.issues or [] if i.get("code") not in INFO_CODES), None)
    return {
        "id": str(document.id),
        "status": document.status,
        "schema": {"slug": document.schema_slug, "version": document.schema_version},
        "title": title,
        "summary": summary,
        "issue": issue,
        "created_at": document.created_at,
        "finished_at": document.finished_at,
    }


@router.get(
    "",
    summary="Список документов",
    description=(
        "Документы тенанта, новые сверху. `status` — фильтр, `limit` 1–100 (по умолчанию 50), `before` — ISO-время: "
        "только документы, созданные раньше (без часового пояса — UTC). `next_before` — значение `before` для "
        "следующей страницы или null, если документов больше нет."
    ),
)
async def list_documents(
    tenant_id: TenantDep,
    session: SessionDep,
    resolver: ResolverDep,
    status_filter: Annotated[DocStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    before: datetime | None = None,
) -> dict:
    query = select(Document).where(Document.tenant_id == tenant_id)
    if status_filter is not None:
        query = query.where(Document.status == status_filter)
    if before is not None:
        if before.tzinfo is None:
            before = before.replace(tzinfo=UTC)
        query = query.where(Document.created_at < before)
    rows = (
        (await session.execute(query.order_by(Document.created_at.desc(), Document.id.desc()).limit(limit + 1)))
        .scalars()
        .all()
    )
    page = rows[:limit]
    next_before = page[-1].created_at if len(rows) > limit else None
    # каждая (slug, версия) страницы разрешается один раз, наборы подписей — одним запросом
    schemas: dict[tuple[str, int], DocSchema | None] = {}
    for d in page:
        key = (d.schema_slug, d.schema_version)
        if key not in schemas:
            try:
                schemas[key] = await resolver.resolve(session, tenant_id, *key)
            except LookupError:
                schemas[key] = None
    label_sets = await get_label_sets(session, tenant_id, {d.schema_slug for d in page})
    empty = LabelSet({}, [])
    items = [
        _list_item(d, schemas[(d.schema_slug, d.schema_version)], label_sets.get(d.schema_slug, empty)) for d in page
    ]
    return {"items": items, "next_before": next_before}


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
    return document_payload(document, settings.public_base_url, await document_exports(session, document.id))


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


DIGITAL_FAILED = "не удалось сформировать цифровую версию: слишком длинные значения"
DIGITAL_CONFLICT = "цифровая версия доступна для документов в статусах ok и needs_review"


def _digital_filename(document: Document) -> str:
    """slug + номер документа латиницей (остальное вырезается) или первые 8 знаков id."""
    number = document.data.get("number") if isinstance(document.data, dict) else None
    clean = re.sub(r"[^A-Za-z0-9_-]", "", str(number or ""))[:40].strip("-_")
    return f"{document.schema_slug}-{clean or str(document.id)[:8]}.pdf"


@router.get(
    "/{document_id}/digital.pdf",
    summary="Цифровая версия документа в PDF",
    description=(
        "PDF, сформированный заново по извлечённым данным (не скан): заголовок, поля, таблицы и служебная строка "
        "со статусом. Только для документов в статусах `ok` и `needs_review` — иначе 409; "
        "чужой или несуществующий документ — 404."
    ),
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}, "description": "Цифровая версия документа"}},
)
async def get_document_digital_pdf(
    document_id: str, tenant_id: TenantDep, session: SessionDep, settings: SettingsDep, schemas: ResolverDep
) -> Response:
    document = await _get_own(session, tenant_id, document_id)
    if document.status not in ("ok", "needs_review"):
        raise HTTPException(status.HTTP_409_CONFLICT, DIGITAL_CONFLICT)
    try:
        schema = await resolve_labeled(
            schemas, session, document.tenant_id, document.schema_slug, document.schema_version
        )
    except SchemaNotFound as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    try:
        pdf = await asyncio.to_thread(
            lambda: render_digital_pdf(
                schema,
                document.data or {},
                status=document.status,
                created_at=document.created_at,
                reviewed=document.reviewed_at is not None,
                font_path=settings.pdf_font_path,
            )
        )
    except Exception:
        logger.exception("цифровая версия документа %s не сформирована", document.id)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, DIGITAL_FAILED) from None
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{_digital_filename(document)}"'},
    )


@router.get(
    "/{document_id}/pages/{n}",
    summary="Превью страницы",
    description="JPEG страницы документа (n с 1). 404 — документа нет, он чужой или превью нет.",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}}, "description": "JPEG страницы"}},
)
async def get_document_page(
    document_id: str, n: int, tenant_id: TenantDep, session: SessionDep, blobs: BlobsDep
) -> Response:
    document = await _get_own(session, tenant_id, document_id)
    if n < 1:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "страница не найдена")
    try:
        image = await blobs.get(page_key(str(document.id), n))
    except BlobNotFound:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "страница не найдена") from None
    return Response(content=image, media_type="image/jpeg", headers={"Cache-Control": "private, max-age=3600"})


class ReviewIn(BaseModel):
    decision: Literal["approve", "reject"]
    data: dict | None = Field(default=None, description="Исправленные данные; без них подтверждаются как есть")
    note: str | None = Field(default=None, max_length=REVIEW_NOTE_MAX)


@router.post(
    "/{document_id}/review",
    summary="Ручная проверка документа",
    description=(
        "Только для документа в `needs_review`. `approve` ставит `ok`; если переданы `data` — это ПОЛНЫЙ объект данных "
        "(пропущенные поля станут пустыми), он проходит ту же "
        "нормализацию, справочники и проверки, что ответ модели (оставшиеся замечания сохраняются, статус всё "
        "равно `ok`). `reject` ставит `failed`. После успеха уходят вебхуки: `document.reviewed` для approve, "
        "`document.failed` для reject; для approve по правилам маршрутизации ставятся выгрузки и уведомления "
        "«новый документ» (согласование считается полученным). 409 — документ уже проверен "
        "другим оператором или ещё обрабатывается."
    ),
)
async def review_document_endpoint(
    document_id: str,
    body: ReviewIn,
    principal: PrincipalDep,
    session: SessionDep,
    settings: SettingsDep,
    schemas: ResolverDep,
    enqueuer: EnqueuerDep,
) -> dict:
    document = await _get_own(session, principal.tenant_id, document_id)
    try:
        document = await review_document(
            session,
            document_id=document.id,
            tenant_id=principal.tenant_id,
            principal=principal,
            decision=body.decision,
            data=body.data,
            note=body.note,
            schemas=schemas,
        )
    except ReviewConflict:
        raise HTTPException(status.HTTP_409_CONFLICT, REVIEW_CONFLICT) from None
    except SchemaNotFound as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    try:
        await enqueue_webhooks(
            session, document, enqueuer, event=EVENT_REVIEWED if body.decision == "approve" else None
        )
    except Exception:
        # проверка уже записана; закоммиченные доставки поднимет requeue_stale
        logger.exception("не удалось поставить вебхуки документа %s", document.id)
    if body.decision == "approve":
        try:
            await route_approved(session, document, schemas, enqueuer)
        except Exception:
            # проверка уже записана; закоммиченные выгрузки поднимет requeue_stale_exports
            logger.exception("не удалось поставить выгрузки документа %s", document.id)
    return document_payload(document, settings.public_base_url, await document_exports(session, document.id))
