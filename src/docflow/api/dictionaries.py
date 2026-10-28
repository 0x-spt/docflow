"""Справочники тенанта (клиники, врачи, контрагенты): с ними сверяются поля с правилом `dictionary`."""

from typing import Any

from fastapi import APIRouter, HTTPException, Path, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from docflow.api.deps import SessionDep, TenantDep
from docflow.db.models import Dictionary
from docflow.validation.dictionaries import dict_key

router = APIRouter(prefix="/v1/dictionaries", tags=["dictionaries"])

NOT_FOUND = "справочник не найден"
MAX_ENTRIES = 5000
NamePath = Path(pattern=r"^[a-z0-9_]{2,40}$", description="Имя, на которое ссылается правило схемы")


class EntryIn(BaseModel):
    value: str = Field(max_length=200, description="Как писать в данных")
    aliases: list[str] = Field(default_factory=list, max_length=20, description="Другие написания того же")

    @field_validator("value")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not dict_key(v):
            raise ValueError("пустое значение")
        return v.strip()

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, v: list[str]) -> list[str]:
        out = [a.strip() for a in v]
        if any(not dict_key(a) or len(a) > 200 for a in out):
            raise ValueError("алиас пуст или длиннее 200 символов")
        return out


class DictionaryIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    entries: list[EntryIn] = Field(max_length=MAX_ENTRIES)

    @field_validator("entries")
    @classmethod
    def _unique(cls, v: list[EntryIn]) -> list[EntryIn]:
        # одно написание у двух записей сделало бы сопоставление неоднозначным
        seen: dict[str, str] = {}
        for e in v:
            for spelling in (e.value, *e.aliases):
                k = dict_key(spelling)
                if k in seen and seen[k] != e.value:
                    raise ValueError(f"«{spelling}» совпадает с «{seen[k]}» (регистр, пробелы и знаки не различаются)")
                seen[k] = e.value
        return v


def _public(d: Dictionary, *, entries: bool = True) -> dict[str, Any]:
    out: dict[str, Any] = {
        "name": d.name,
        "title": d.title,
        "size": len(d.entries),
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }
    if entries:
        out["entries"] = list(d.entries)
    return out


async def _get(session, tenant_id, name: str) -> Dictionary | None:
    q = select(Dictionary).where(Dictionary.tenant_id == tenant_id, Dictionary.name == name)
    return (await session.execute(q)).scalar_one_or_none()


@router.get("", summary="Список справочников", description="Имена, названия и размер; без записей.")
async def list_dictionaries(tenant_id: TenantDep, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.execute(select(Dictionary).where(Dictionary.tenant_id == tenant_id).order_by(Dictionary.name))
    ).scalars()
    return [_public(d, entries=False) for d in rows]


@router.get("/{name}", summary="Справочник целиком")
async def get_dictionary(tenant_id: TenantDep, session: SessionDep, name: str = NamePath) -> dict[str, Any]:
    d = await _get(session, tenant_id, name)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return _public(d)


@router.put(
    "/{name}",
    summary="Создать или заменить справочник",
    description=(
        "Записи заменяются целиком (до 5000). Совпадения ищутся без учёта регистра, пунктуации, пробелов и ё/е; "
        "близкие написания (одна-две буквы) исправляются на значение из справочника. "
        "Новые документы сверяются с новой версией сразу; уже обработанные не пересчитываются."
    ),
)
async def put_dictionary(
    body: DictionaryIn, tenant_id: TenantDep, session: SessionDep, name: str = NamePath
) -> dict[str, Any]:
    entries = [e.model_dump() for e in body.entries]
    for attempt in range(2):  # гонка двух первых PUT: второй получит IntegrityError и обновит запись
        d = await _get(session, tenant_id, name)
        if d is None:
            d = Dictionary(tenant_id=tenant_id, name=name, title=body.title, entries=entries)
            session.add(d)
        else:
            d.title, d.entries = body.title, entries
        try:
            await session.commit()
            break
        except IntegrityError:
            await session.rollback()
            if attempt:
                raise
    await session.refresh(d)
    return _public(d)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT, summary="Удалить справочник")
async def delete_dictionary(tenant_id: TenantDep, session: SessionDep, name: str = NamePath) -> Response:
    d = await _get(session, tenant_id, name)
    if d is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    await session.delete(d)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
