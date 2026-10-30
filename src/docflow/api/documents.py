"""Загрузка документов, статус и готовый PDF."""

import asyncio
import logging
import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Response, UploadFile, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
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
from docflow.db.models import Document
from docflow.exports import enqueue_bot_messages
from docflow.intake import (
    IntakeConflict,
    IntakeDeps,
    IntakeEnqueueFailed,
    IntakeError,
    IntakeFile,
    IntakeInvalid,
    IntakeLimitExceeded,
    IntakeStorageFailed,
    IntakeTooLarge,
    IntakeUnsupported,
    accept_document,
    retype_document,
)
from docflow.labels import LabelSet, apply_labels, get_label_sets, queue_fields_for, resolve_labeled
from docflow.pdf.digital import render_digital_pdf
from docflow.pipeline import SchemaNotFound
from docflow.review import ReviewConflict, review_document, route_approved
from docflow.schemas.model import DocSchema
from docflow.serializers import digital_filename, document_exports, document_payload
from docflow.storage.blobs import BlobNotFound, page_key, scan_key
from docflow.validation.engine import INFO_CODES
from docflow.webhooks import EVENT_REVIEWED, enqueue_webhooks

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/documents", tags=["documents"])

DocStatus = Literal["queued", "processing", "ok", "needs_review", "failed"]
SUMMARY_FIELDS = 3
CHUNK = 1024 * 1024
NOT_FOUND = "документ не найден"
IDEMPOTENCY_KEY_MAX = 255
REVIEW_CONFLICT = "документ уже проверен или ещё обрабатывается"
REVIEW_NOTE_MAX = 1000

# сигнатура → MIME для хранилища; заголовку клиента не верим
_INTAKE_STATUS: dict[type[IntakeError], int] = {
    IntakeConflict: status.HTTP_409_CONFLICT,
    IntakeInvalid: status.HTTP_422_UNPROCESSABLE_CONTENT,
    IntakeTooLarge: status.HTTP_413_CONTENT_TOO_LARGE,
    IntakeUnsupported: status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    IntakeLimitExceeded: status.HTTP_402_PAYMENT_REQUIRED,
    IntakeStorageFailed: status.HTTP_500_INTERNAL_SERVER_ERROR,
    IntakeEnqueueFailed: status.HTTP_503_SERVICE_UNAVAILABLE,
}


def _accepted(document: Document) -> dict:
    return {"id": str(document.id), "status": document.status}


async def _read_capped(upload: UploadFile, limit: int, max_mb: int) -> bytes:
    """Читает файл кусками и бросает IntakeTooLarge, как только набралось больше лимита: 2 ГБ в память не попадут."""
    chunks: list[bytes] = []
    size = 0
    while chunk := await upload.read(CHUNK):
        size += len(chunk)
        if size > limit:
            raise IntakeTooLarge(f"Файл «{upload.filename}» больше {max_mb} МБ")
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
        "`schema=auto` — тип определит сервер по первой странице (в документе сначала `auto`, версия 0); "
        "`schema_version` для `auto` игнорируется. "
        "Повтор с тем же заголовком `Idempotency-Key` (до 255 символов) вернёт 200 и исходный документ без "
        "повторной обработки — даже если файлы в повторе другие: они не читаются и не проверяются. "
        "Ошибки: 413 — файл или весь запрос больше лимита, 415 — неподдерживаемый или битый файл, "
        "422 — неизвестная схема, неверный режим, нет файлов, слишком много файлов или страниц; "
        "402 — страницы месяца по тарифу исчерпаны."
    ),
    responses={200: {"description": "Документ с этим Idempotency-Key уже был принят"}},
)
async def upload_document(
    principal: PrincipalDep,
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
    intake_files = [
        IntakeFile(
            upload.filename or "file",
            lambda limit, upload=upload: _read_capped(upload, limit, settings.max_file_mb),
        )
        for upload in files or []
    ]
    try:
        result = await accept_document(
            session,
            IntakeDeps(settings=settings, blobs=blobs, enqueuer=enqueuer, resolver=resolver),
            tenant_id=principal.tenant_id,
            schema_slug=schema_slug,
            files=intake_files,
            schema_version=schema_version,
            mode=mode,
            idempotency_key=idempotency_key,
            source="cabinet" if principal.user_id else "api",
        )
    except IntakeError as exc:
        raise HTTPException(_INTAKE_STATUS[type(exc)], exc.message) from None
    code = status.HTTP_202_ACCEPTED if result.created else status.HTTP_200_OK
    return JSONResponse(_accepted(result.document), status_code=code)


def _scalar(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return False
    return not (isinstance(value, str) and not value.strip())


def _list_item(document: Document, schema: DocSchema | None, label_set: LabelSet, titles: dict[str, str]) -> dict:
    data = document.data or {}
    number = data.get("number")
    has_number = isinstance(number, str | int | float) and not isinstance(number, bool) and str(number).strip()
    title = str(number) if has_number else str(document.id)[:8]
    summary = []
    if schema is not None:
        # поля и подписи выбрала компания (или умолчание схемы); пустые значения пропускаем
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
    # названия полей с подписями компании — один раз на (slug, версию), а не на каждую строку
    titles: dict[tuple[str, int], dict[str, str]] = {}
    for key, schema in schemas.items():
        if schema is not None:
            labeled = apply_labels(schema, label_sets.get(key[0], empty).labels)
            titles[key] = {f.name: f.title or f.name for f in labeled.fields}
    items = [
        _list_item(
            d,
            schemas[(d.schema_slug, d.schema_version)],
            label_sets.get(d.schema_slug, empty),
            titles.get((d.schema_slug, d.schema_version), {}),
        )
        for d in page
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
        headers={"Content-Disposition": f'inline; filename="{digital_filename(document)}"'},
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


class RetypeIn(BaseModel):
    schema_slug: str = Field(alias="schema", description="slug выбранного типа документа, не `auto`")


@router.post(
    "/{document_id}/retype",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Распознать заново с выбранным типом",
    description=(
        "Только для документа, который упал при определении типа (`failed`, схема `auto`, модель не вызывалась): "
        "ставит те же файлы в очередь с выбранным типом (последняя версия). Возвращает id и статус `queued`. "
        "409 — тип документа уже известен или документ не в `failed` (в том числе повторный запрос); "
        "422 — `schema` равна `auto` или такой схемы нет; 402 — страницы месяца по тарифу исчерпаны; "
        "503 — не удалось поставить в очередь, документ остаётся `failed`; 404 — чужой или несуществующий."
    ),
)
async def retype_document_endpoint(
    document_id: str,
    body: RetypeIn,
    tenant_id: TenantDep,
    session: SessionDep,
    enqueuer: EnqueuerDep,
    resolver: ResolverDep,
) -> dict:
    document = await _get_own(session, tenant_id, document_id)
    try:
        document = await retype_document(
            session, resolver, enqueuer, tenant_id=tenant_id, document_id=document.id, schema_slug=body.schema_slug
        )
    except IntakeError as exc:
        raise HTTPException(_INTAKE_STATUS[type(exc)], exc.message) from None
    return _accepted(document)


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
        "«новый документ» (согласование считается полученным); подключённому Telegram-боту — сообщение о решении. "
        "409 — документ уже проверен "
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
    try:
        await enqueue_bot_messages(session, document, "bot_reviewed", enqueuer)
    except Exception:
        # проверка уже записана; закоммиченные сообщения поднимет requeue_stale_exports
        logger.exception("не удалось поставить сообщения бота о документе %s", document.id)
    if body.decision == "approve":
        try:
            await route_approved(session, document, schemas, enqueuer)
        except Exception:
            # проверка уже записана; закоммиченные выгрузки поднимет requeue_stale_exports
            logger.exception("не удалось поставить выгрузки документа %s", document.id)
    return document_payload(document, settings.public_base_url, await document_exports(session, document.id))
