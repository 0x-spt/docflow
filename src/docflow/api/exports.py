"""Журнал выгрузок и уведомлений: куда ушёл документ, ID во внешней системе, ошибка, «Повторить».

Читать может любой пользователь тенанта и ключ API; «Повторить» — владелец или ключ API.
"""

import asyncio
import logging
import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select, update

from docflow.api.deps import (
    BlobsDep,
    EnqueuerDep,
    Principal,
    ResolverDep,
    SessionDep,
    SettingsDep,
    TenantDep,
    parse_id,
    require_owner_or_key,
)
from docflow.db.models import Destination, Document, Export
from docflow.export_xlsx import build_registry
from docflow.exports import build_export_doc, export_job_id
from docflow.queue import DELIVER_EXPORT
from docflow.serializers import export_item

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/exports", tags=["exports"])

ManagerDep = Annotated[Principal, Depends(require_owner_or_key)]
NOT_FOUND = "выгрузка не найдена"
RETRY_CONFLICT = "повторить можно только неудавшуюся выгрузку"
ExportStatus = Literal["pending", "done", "failed"]
MAX_EXCEL_DOCS = 5000
TOO_MANY = "слишком много документов — сузьте период"
XLSX_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _item(export: Export, name: str | None, kind: str | None) -> dict[str, Any]:
    return {
        **export_item(export, name, kind),
        "document_id": str(export.document_id),
        "attempt": export.attempt,
        "next_attempt_at": _iso(export.next_attempt_at),
        "created_at": _iso(export.created_at),
    }


async def _one(session, tenant_id: uuid.UUID, export_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        await session.execute(
            select(Export, Destination.name, Destination.kind)
            .outerjoin(Destination, Destination.id == Export.destination_id)
            .where(Export.id == export_id, Export.tenant_id == tenant_id)
        )
    ).one_or_none()
    return _item(*row) if row is not None else None


@router.get(
    "",
    summary="Журнал выгрузок",
    description=(
        "Выгрузки и уведомления тенанта, новые сверху. Фильтры: `document_id`, `status` (pending, done, failed); "
        "`limit` 1–200 (по умолчанию 50). `error` — причина последней неудачи; `external_id` — ID созданной "
        "сущности во внешней системе. Удалённый получатель — `destination_id: null`."
    ),
)
async def list_exports(
    tenant_id: TenantDep,
    session: SessionDep,
    document_id: uuid.UUID | None = None,
    status_filter: Annotated[ExportStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> dict[str, Any]:
    query = (
        select(Export, Destination.name, Destination.kind)
        .outerjoin(Destination, Destination.id == Export.destination_id)
        .where(Export.tenant_id == tenant_id)
    )
    if document_id is not None:
        query = query.where(Export.document_id == document_id)
    if status_filter is not None:
        query = query.where(Export.status == status_filter)
    rows = await session.execute(query.order_by(Export.created_at.desc(), Export.id.desc()).limit(limit))
    return {"items": [_item(*row) for row in rows.all()]}


@router.get(
    "/excel",
    summary="Реестр документов в Excel",
    description=(
        "xlsx с листами «Документы» и «Позиции» за период (по дате загрузки, границы включительно). "
        "`schema` — slug типа документа (обязателен); `status`: ok (по умолчанию), needs_review или all. "
        "Заголовки — по последней версии схемы, данные — каждого документа свои. Больше 5000 документов — 422."
    ),
)
async def excel_registry(
    tenant_id: TenantDep,
    session: SessionDep,
    resolver: ResolverDep,
    settings: SettingsDep,
    blobs: BlobsDep,
    schema: str,
    date_from: Annotated[date, Query(alias="from")],
    date_to: Annotated[date, Query(alias="to")],
    status_filter: Annotated[Literal["ok", "needs_review", "all"], Query(alias="status")] = "ok",
) -> Response:
    if date_to < date_from:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "конец периода раньше начала")
    try:
        latest = await resolver.resolve(session, tenant_id, schema, None)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    start = datetime.combine(date_from, time.min, tzinfo=UTC)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=UTC)
    where = [
        Document.tenant_id == tenant_id,
        Document.schema_slug == schema,
        Document.created_at >= start,
        Document.created_at < end,
        Document.status.in_(("ok", "needs_review") if status_filter == "all" else (status_filter,)),
    ]
    count = (await session.execute(select(func.count()).select_from(Document).where(*where))).scalar_one()
    if count > MAX_EXCEL_DOCS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, TOO_MANY)
    documents = (
        await session.execute(select(Document).where(*where).order_by(Document.created_at, Document.id))
    ).scalars().all()
    docs = [build_export_doc(d, latest, settings, blobs) for d in documents]
    content = await asyncio.to_thread(build_registry, latest, docs)
    filename = f"docflow-{schema}-{date_from.isoformat()}-{date_to.isoformat()}.xlsx"
    return Response(
        content,
        media_type=XLSX_TYPE,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/{export_id}/retry",
    summary="Повторить выгрузку",
    description=(
        "Только для выгрузки в статусе `failed`: возвращает её в очередь с нуля попыток. "
        "409 — выгрузка уже выполнена или ещё в работе; 404 — чужая или несуществующая; "
        "403 — сессия оператора (нужен владелец или ключ API)."
    ),
)
async def retry_export(
    export_id: str, manager: ManagerDep, session: SessionDep, enqueuer: EnqueuerDep
) -> dict[str, Any]:
    eid = parse_id(export_id)
    export = await session.get(Export, eid)
    if export is None or export.tenant_id != manager.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    # атомарно: двойное нажатие поставит задачу один раз
    res = await session.execute(
        update(Export)
        .where(Export.id == eid, Export.tenant_id == manager.tenant_id, Export.status == "failed")
        .values(status="pending", attempt=0, last_error=None, next_attempt_at=datetime.now(UTC))
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    if res.rowcount != 1:
        raise HTTPException(status.HTTP_409_CONFLICT, RETRY_CONFLICT)
    try:
        await enqueuer.enqueue(DELIVER_EXPORT, str(eid), 0, job_id=export_job_id(eid, 0))
    except Exception:
        # строка уже pending: её поднимет requeue_stale_exports
        logger.exception("не удалось поставить выгрузку %s в очередь", eid)
    session.expire_all()
    return await _one(session, manager.tenant_id, eid)
