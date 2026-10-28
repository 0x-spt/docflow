"""Зависимости запросов: всё берётся из app.state, которое заполняет create_app (тесты) или lifespan (прод)."""

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.config import Settings
from docflow.db.models import ApiKey, User, hash_key
from docflow.db.models import Session as UserSession
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


@dataclass
class Principal:
    """Кто пришёл: ключ API (user_id=None) или сессия человека."""

    tenant_id: uuid.UUID
    user_id: uuid.UUID | None = None
    role: str | None = None
    session_id: uuid.UUID | None = None


async def get_principal(request: Request, session: Annotated[AsyncSession, Depends(get_session)]) -> Principal:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer":
        raise _unauthorized()
    if token.startswith("dfk_"):
        tenant_id = (
            await session.execute(
                select(ApiKey.tenant_id).where(ApiKey.key_hash == hash_key(token), ApiKey.revoked_at.is_(None))
            )
        ).scalar_one_or_none()
        if tenant_id is None:
            raise _unauthorized()
        return Principal(tenant_id=tenant_id)
    if token.startswith("dfs_"):
        row = (
            await session.execute(
                select(UserSession.id, User.id, User.tenant_id, User.role)
                .join(User, User.id == UserSession.user_id)
                .where(
                    UserSession.token_hash == hash_key(token),
                    UserSession.revoked_at.is_(None),
                    UserSession.expires_at > datetime.now(UTC),
                    User.disabled_at.is_(None),
                )
            )
        ).one_or_none()
        if row is not None:
            session_id, user_id, tenant_id, role = row
            return Principal(tenant_id=tenant_id, user_id=user_id, role=role, session_id=session_id)
    raise _unauthorized()


PrincipalDep = Annotated[Principal, Depends(get_principal)]


async def get_tenant_id(principal: PrincipalDep) -> uuid.UUID:
    return principal.tenant_id


def require_owner(principal: PrincipalDep) -> Principal:
    if principal.user_id is None or principal.role != "owner":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "нужны права владельца")
    return principal


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
