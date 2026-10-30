"""Схемы документов: встроенные и свои у каждого тенанта, с версиями."""

import logging
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from docflow.api.deps import Principal, ResolverDep, SessionDep, TenantDep, require_owner_or_key
from docflow.db.models import FieldLabels, SchemaRecord
from docflow.labels import (
    LabelSet,
    LabelsInvalid,
    apply_labels,
    check_label_set,
    get_label_set,
    label_keys,
    prune_label_set,
    queue_fields_for,
)
from docflow.schemas.catalog import AUTO_SLUG, list_doc_types
from docflow.schemas.library import builtin_schemas
from docflow.schemas.llm import to_json_schema
from docflow.schemas.model import DocSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/schemas", tags=["schemas"])

SAVE_ATTEMPTS = 3
# служебные имена: auto — «определить тип», unknown — «не знаю» в перечне классификации
RESERVED_SLUGS = (AUTO_SLUG, "unknown")

ManagerDep = Annotated[Principal, Depends(require_owner_or_key)]


@router.get(
    "",
    summary="Список схем",
    description=(
        "Последняя версия каждой схемы, доступной тенанту: встроенные и свои. "
        "Своя схема с тем же slug заменяет встроенную (`builtin: false`)."
    ),
)
async def list_schemas(tenant_id: TenantDep, session: SessionDep) -> list[dict[str, Any]]:
    return [
        {"slug": t.slug, "version": t.version, "title": t.title, "builtin": t.builtin}
        for t in await list_doc_types(session, tenant_id)
    ]


async def _latest_visible_version(session, tenant_id, slug: str) -> int:
    """Старшая версия slug среди своих схем тенанта, встроенных в БД и встроенных в библиотеке.

    Своя версия не должна совпадать со встроенной: иначе `version=1` у загрузки значил бы то встроенную,
    то перекрытие — смотря есть ли у тенанта своя схема.
    """
    stored = (
        await session.execute(
            select(func.max(SchemaRecord.version)).where(
                SchemaRecord.slug == slug,
                or_(SchemaRecord.tenant_id == tenant_id, SchemaRecord.tenant_id.is_(None)),
            )
        )
    ).scalar_one_or_none()
    library = builtin_schemas().get(slug)
    return max(stored or 0, library.version if library else 0)


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Добавить схему",
    description=(
        "Тело — схема документа (поля, правила проверки). Версия назначается сервером: следующая за последней "
        "видимой тенанту версией этого slug (своей или встроенной), 1 для нового slug; `version` из тела "
        "игнорируется. Можно перекрыть встроенную схему, взяв её slug: перекрытие встроенной v1 станет v2, "
        "и `version=1` по-прежнему означает встроенную. Ошибка в схеме — 422, `loc` указывает на поле или правило. "
        "`expected_version` — версия, которую видел редактор (`GET ...?raw=true`, 0 — схемы ещё нет): если схему "
        "успели изменить, 409. Подписи и поля очереди удалённых полей убираются в той же транзакции. "
        "Имена `auto` и `unknown` зарезервированы — 422. "
        "403 — сессия оператора (нужен владелец или ключ API)."
    ),
)
async def create_schema(
    schema: DocSchema,
    manager: ManagerDep,
    session: SessionDep,
    resolver: ResolverDep,
    expected_version: int | None = Query(None, ge=0, description="Версия схемы, с которой начал редактор"),
) -> dict[str, Any]:
    tenant_id = manager.tenant_id
    if schema.slug in RESERVED_SLUGS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Служебное имя «{schema.slug}» зарезервировано")
    for _ in range(SAVE_ATTEMPTS):
        if expected_version is not None:
            try:
                seen = (await resolver.resolve(session, tenant_id, schema.slug, None)).version
            except LookupError:
                seen = 0
            if seen != expected_version:
                if expected_version == 0:
                    msg = f"Тип документа «{schema.slug}» уже есть: выберите другое служебное имя"
                else:
                    msg = (
                        f"Тип документа изменили после открытия редактора (сейчас версия {seen}): "
                        "обновите страницу"
                    )
                raise HTTPException(status.HTTP_409_CONFLICT, msg)
        current = await _latest_visible_version(session, tenant_id, schema.slug)
        stored = schema.model_copy(update={"version": current + 1})
        body = stored.model_dump(mode="json")
        # чистка до add: её SELECT сбросил бы INSERT вне try, и коллизия версии дала бы 500 вместо повтора
        await prune_label_set(session, tenant_id, stored)
        session.add(SchemaRecord(tenant_id=tenant_id, slug=stored.slug, version=stored.version, body=body))
        try:
            await session.commit()
        except IntegrityError:
            # параллельный POST занял ту же версию — берём следующую
            await session.rollback()
            continue
        return stored.model_dump(mode="json")
    raise HTTPException(status.HTTP_409_CONFLICT, "Схему одновременно меняют другие запросы, повторите позже")


async def _resolve(resolver, session, tenant_id, slug: str, version: int | None) -> DocSchema:
    try:
        return await resolver.resolve(session, tenant_id, slug, version)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None


class LabelsIn(BaseModel):
    labels: dict[str, str] = Field(
        default_factory=dict, max_length=500, description="Ключ поля («vat», «items.qty») -> название"
    )
    queue_fields: list[str] = Field(
        default_factory=list, max_length=50, description="Поля верхнего уровня для очереди, до 3"
    )


def _labels_view(schema: DocSchema, stored: LabelSet) -> dict[str, Any]:
    """Таблица подписей схемы: умолчание, подпись компании, вошло ли поле в очередь."""
    specs: dict[str, tuple[Any, str | None]] = {}
    for f in schema.fields:
        specs[f.name] = (f, None)
    for f in schema.fields:
        for c in f.columns or []:
            specs[f"{f.name}.{c.name}"] = (c, f.name)
    queue = queue_fields_for(schema, stored.queue_fields)
    top = {f.name for f in schema.fields if f.type != "table"}
    rows = [
        {
            "key": key,
            "type": specs[key][0].type,
            "group": specs[key][1],
            "default": specs[key][0].title or specs[key][0].name,
            "label": stored.labels.get(key, ""),
            "queue": key in queue,
        }
        for key in label_keys(schema)
    ]
    return {
        "slug": schema.slug,
        "title": schema.title,
        "version": schema.version,
        "rows": rows,
        "queue_fields": queue,
        # своего набора нет (или он целиком устарел): очередь по умолчанию
        "queue_default": not any(k in top for k in stored.queue_fields),
    }


@router.get(
    "/{slug}/labels",
    summary="Названия полей компании",
    description=(
        "Все поля последней версии схемы: наше название (`default`), название компании (`label`, пусто — своего "
        "нет) и входит ли поле в очередь. `queue_default` — набор полей очереди не задан, действует умолчание."
    ),
)
async def get_labels(slug: str, tenant_id: TenantDep, session: SessionDep, resolver: ResolverDep) -> dict[str, Any]:
    schema = await _resolve(resolver, session, tenant_id, slug, None)
    return _labels_view(schema, await get_label_set(session, tenant_id, slug))


@router.put(
    "/{slug}/labels",
    summary="Сохранить названия полей",
    description=(
        "Заменяет набор целиком: названия компании и до 3 полей очереди. Название — до 80 символов, без управляющих "
        "символов, не повторяет название другого поля и не занимает служебную колонку таблиц. Проверка по последней "
        "версии схемы; ответ как у GET. 422 — ошибки по полям. 403 — сессия оператора (нужен владелец или ключ API)."
    ),
)
async def put_labels(
    slug: str, body: LabelsIn, manager: ManagerDep, session: SessionDep, resolver: ResolverDep
) -> dict[str, Any]:
    tenant_id = manager.tenant_id
    schema = await _resolve(resolver, session, tenant_id, slug, None)
    try:
        clean = check_label_set(schema, body.labels, body.queue_fields)
    except LabelsInvalid as exc:
        detail = [{"loc": ["body", *loc], "msg": msg, "type": "value_error"} for loc, msg in exc.errors]
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail) from None
    for attempt in range(2):
        row = (
            await session.execute(
                select(FieldLabels).where(FieldLabels.tenant_id == tenant_id, FieldLabels.schema_slug == slug)
            )
        ).scalar_one_or_none()
        if row is None:
            row = FieldLabels(tenant_id=tenant_id, schema_slug=slug)
            session.add(row)
        row.labels = clean.labels
        row.queue_fields = clean.queue_fields
        row.updated_at = datetime.now(UTC)
        try:
            await session.commit()
            break
        except IntegrityError:
            # параллельный PUT создал строку первым — перечитываем и обновляем её
            await session.rollback()
            if attempt:
                raise HTTPException(status.HTTP_409_CONFLICT, "Названия одновременно меняют, повторите позже") from None
    return _labels_view(schema, clean)


@router.get(
    "/{slug}",
    summary="Схема по slug",
    description=(
        "Своя схема тенанта, иначе встроенная. По умолчанию названия полей (`title`) — свои у компании, если "
        "заданы. Клиенту, который правит схему и загружает её новой версией, нужен `raw=true`: без подписей "
        "компании, иначе они попадут в саму схему. Без `version` — последняя версия. "
        "`builtin=true` — встроенная схема без перекрытия тенанта (для отката к ней). Нет такой — 404."
    ),
)
async def get_schema(
    slug: str,
    tenant_id: TenantDep,
    session: SessionDep,
    resolver: ResolverDep,
    version: int | None = Query(None, description="Версия схемы"),
    raw: bool = Query(False, description="Без подписей компании: схема, как она хранится"),
    builtin: bool = Query(False, description="Встроенная схема без перекрытия тенанта"),
) -> dict[str, Any]:
    try:
        schema = await resolver.resolve(session, None if builtin else tenant_id, slug, version)
        if not raw:
            schema = apply_labels(schema, (await get_label_set(session, tenant_id, slug)).labels)
    except LookupError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from None
    return schema.model_dump(mode="json")


@router.get(
    "/{slug}/json-schema",
    summary="JSON Schema для модели",
    description="JSON Schema ответа, которую получает модель при извлечении данных по этой схеме.",
)
async def get_json_schema(
    slug: str,
    tenant_id: TenantDep,
    session: SessionDep,
    resolver: ResolverDep,
    version: int | None = Query(None, description="Версия схемы"),
) -> dict[str, Any]:
    return to_json_schema(await _resolve(resolver, session, tenant_id, slug, version))
