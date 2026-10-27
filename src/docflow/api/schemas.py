"""Схемы документов: встроенные и свои у каждого тенанта, с версиями."""

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from docflow.api.deps import ResolverDep, SessionDep, TenantDep
from docflow.db.models import SchemaRecord
from docflow.schemas.library import builtin_schemas
from docflow.schemas.llm import to_json_schema
from docflow.schemas.model import DocSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/schemas", tags=["schemas"])

SAVE_ATTEMPTS = 3


@router.get(
    "",
    summary="Список схем",
    description=(
        "Последняя версия каждой схемы, доступной тенанту: встроенные и свои. "
        "Своя схема с тем же slug заменяет встроенную (`builtin: false`)."
    ),
)
async def list_schemas(tenant_id: TenantDep, session: SessionDep) -> list[dict[str, Any]]:
    items: dict[str, dict[str, Any]] = {
        slug: {"slug": slug, "version": s.version, "title": s.title, "builtin": True}
        for slug, s in builtin_schemas().items()
    }
    rows = (
        await session.execute(
            select(SchemaRecord.tenant_id, SchemaRecord.slug, SchemaRecord.version, SchemaRecord.body)
            .where(or_(SchemaRecord.tenant_id.is_(None), SchemaRecord.tenant_id == tenant_id))
            # встроенные раньше своих, внутри — по возрастанию версии: последняя запись побеждает
            .order_by(SchemaRecord.tenant_id.is_not(None), SchemaRecord.version)
        )
    ).all()
    for owner, slug, version, body in rows:
        builtin = owner is None
        current = items.get(slug)
        if builtin and current is not None and current["version"] > version:
            continue
        items[slug] = {"slug": slug, "version": version, "title": body.get("title", slug), "builtin": builtin}
    return sorted(items.values(), key=lambda i: i["slug"])


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
        "и `version=1` по-прежнему означает встроенную. Ошибка в схеме — 422."
    ),
)
async def create_schema(schema: DocSchema, tenant_id: TenantDep, session: SessionDep) -> dict[str, Any]:
    for _ in range(SAVE_ATTEMPTS):
        current = await _latest_visible_version(session, tenant_id, schema.slug)
        stored = schema.model_copy(update={"version": current + 1})
        body = stored.model_dump(mode="json")
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


@router.get(
    "/{slug}",
    summary="Схема по slug",
    description="Своя схема тенанта, иначе встроенная. Без `version` — последняя версия. Нет такой — 404.",
)
async def get_schema(
    slug: str,
    tenant_id: TenantDep,
    session: SessionDep,
    resolver: ResolverDep,
    version: int | None = Query(None, description="Версия схемы"),
) -> dict[str, Any]:
    return (await _resolve(resolver, session, tenant_id, slug, version)).model_dump(mode="json")


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
