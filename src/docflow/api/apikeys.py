"""Ключи API тенанта из кабинета: только сессия владельца (ключом API ключами управлять нельзя)."""

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select

from docflow.api.deps import Principal, SessionDep, parse_id, require_owner
from docflow.db.models import ApiKey, generate_api_key

router = APIRouter(prefix="/v1/api-keys", tags=["api-keys"])

NOT_FOUND = "ключ не найден"
OwnerDep = Annotated[Principal, Depends(require_owner)]


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


@router.get(
    "",
    summary="Ключи API",
    description="Префикс, даты создания и отзыва; сам ключ не хранится и не показывается.",
)
async def list_keys(owner: OwnerDep, session: SessionDep) -> list[dict]:
    rows = (
        await session.execute(select(ApiKey).where(ApiKey.tenant_id == owner.tenant_id).order_by(ApiKey.created_at))
    ).scalars()
    return [
        {"id": str(k.id), "prefix": k.prefix, "created_at": _iso(k.created_at), "revoked_at": _iso(k.revoked_at)}
        for k in rows
    ]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Выпустить ключ",
    description="Ключ в ответе показывается один раз: в базе остаётся только его хеш.",
)
async def create_key(owner: OwnerDep, session: SessionDep) -> dict:
    plain, prefix, key_hash = generate_api_key()
    key = ApiKey(tenant_id=owner.tenant_id, prefix=prefix, key_hash=key_hash)
    session.add(key)
    await session.commit()
    await session.refresh(key)
    return {"id": str(key.id), "prefix": prefix, "key": plain, "created_at": _iso(key.created_at)}


@router.delete("/{key_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Отозвать ключ")
async def revoke_key(key_id: str, owner: OwnerDep, session: SessionDep) -> Response:
    key = await session.get(ApiKey, parse_id(key_id))
    if key is None or key.tenant_id != owner.tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    if key.revoked_at is None:
        key.revoked_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
