"""Зависимости запросов: всё берётся из app.state, которое заполняет create_app (тесты) или lifespan (прод)."""

import uuid
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.config import Settings
from docflow.db.models import ApiKey, hash_key
from docflow.pipeline import SchemaResolver
from docflow.queue import Enqueuer
from docflow.storage.blobs import BlobStore

AUTH_ERROR = "неверный или отозванный ключ API"


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    async with request.app.state.sessionmaker() as session:
        yield session


def get_blobs(request: Request) -> BlobStore:
    return request.app.state.blobs


def get_enqueuer(request: Request) -> Enqueuer:
    return request.app.state.enqueuer


def get_resolver(request: Request) -> SchemaResolver:
    return request.app.state.schemas


def _unauthorized() -> HTTPException:
    # одна и та же ошибка на любой сбой: не подсказываем, существует ли ключ и чем он плох
    return HTTPException(status.HTTP_401_UNAUTHORIZED, AUTH_ERROR, headers={"WWW-Authenticate": "Bearer"})


async def get_tenant_id(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> uuid.UUID:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token.startswith("dfk_"):
        raise _unauthorized()
    tenant_id = (
        await session.execute(
            select(ApiKey.tenant_id).where(ApiKey.key_hash == hash_key(token), ApiKey.revoked_at.is_(None))
        )
    ).scalar_one_or_none()
    if tenant_id is None:
        raise _unauthorized()
    return tenant_id


SessionDep = Annotated[AsyncSession, Depends(get_session)]
TenantDep = Annotated[uuid.UUID, Depends(get_tenant_id)]
SettingsDep = Annotated[Settings, Depends(get_app_settings)]
BlobsDep = Annotated[BlobStore, Depends(get_blobs)]
EnqueuerDep = Annotated[Enqueuer, Depends(get_enqueuer)]
ResolverDep = Annotated[SchemaResolver, Depends(get_resolver)]


def parse_id(raw: str) -> uuid.UUID:
    """Кривой id — то же 404, что и несуществующий."""
    try:
        return uuid.UUID(raw)
    except ValueError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "не найдено") from None
