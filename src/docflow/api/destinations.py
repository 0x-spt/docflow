"""Получатели выгрузки: подключения тенанта к системам клиента (1С, CRM, таблицы, Telegram, почта).

Создавать, менять и удалять может владелец (сессия) или ключ API; оператор только читает.
Секреты принимаются, шифруются и никогда не возвращаются — только признак has_secrets.
"""

import asyncio
import dataclasses
from datetime import UTC, datetime
from typing import Annotated, Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field, StrictStr
from sqlalchemy import select

from docflow.api.deps import Principal, SessionDep, SettingsDep, TenantDep, parse_id, require_owner_or_key
from docflow.connectors.base import REGISTRY, Connector, ConnectorError, DestCtx, FieldDef
from docflow.db.models import Destination, RoutingRules
from docflow.secrets import SecretsKeyMissing, decrypt_secrets, encrypt_secrets
from docflow.webhooks import check_webhook_url

router = APIRouter(prefix="/v1/destinations", tags=["destinations"])
catalog_router = APIRouter(prefix="/v1/connectors", tags=["destinations"])

NOT_FOUND = "получатель не найден"
HTTP_TIMEOUT = 20.0

ManagerDep = Annotated[Principal, Depends(require_owner_or_key)]


class DestinationIn(BaseModel):
    kind: str = Field(max_length=50, description="Тип получателя из GET /v1/connectors")
    name: str = Field(min_length=1, max_length=200)
    config: dict[str, Any] = Field(default_factory=dict, description="Несекретные настройки")
    secrets: dict[str, StrictStr] = Field(default_factory=dict, description="Секреты: принимаются, но не возвращаются")
    mapping: dict[str, StrictStr] = Field(default_factory=dict, description="Поле документа -> поле системы")


class DestinationPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    config: dict[str, Any] | None = Field(default=None, description="Заменяет настройки целиком")
    secrets: dict[str, StrictStr] | None = Field(
        default=None, description="Переданные секреты обновляются, непереданные сохраняются"
    )
    mapping: dict[str, StrictStr] | None = Field(default=None, description="Заменяет соответствие полей целиком")
    active: bool | None = None


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def _public(d: Destination) -> dict[str, Any]:
    return {
        "id": str(d.id),
        "kind": d.kind,
        "name": d.name,
        "config": d.config or {},
        "mapping": d.mapping or {},
        "has_secrets": d.secrets_enc is not None,
        "active": d.active,
        "created_at": d.created_at.isoformat() if d.created_at else None,
        "updated_at": d.updated_at.isoformat() if d.updated_at else None,
    }


def _connector(kind: str) -> Connector:
    connector = REGISTRY.get(kind)
    if connector is None:
        raise _unprocessable(f"неизвестный тип получателя «{kind}»; доступные: {', '.join(sorted(REGISTRY))}")
    return connector


def _check_names(given: dict[str, Any], fields: list[FieldDef], what: str) -> None:
    known = {f.name for f in fields}
    unknown = sorted(set(given) - known)
    if unknown:
        raise _unprocessable(f"{what}: неизвестные поля {', '.join(unknown)}")


def _check_required(given: dict[str, Any], fields: list[FieldDef], what: str) -> None:
    for f in fields:
        if f.required and given.get(f.name) in (None, ""):
            raise _unprocessable(f"{what}: заполните «{f.label}»")


async def _check_urls(values: dict[str, Any], fields: list[FieldDef], *, allow_private: bool) -> None:
    """SSRF-проверка полей типа url (и в настройках, и в секретах: адрес вебхука Битрикс24 — секрет)."""
    for f in fields:
        value = values.get(f.name)
        if f.type != "url" or value in (None, ""):
            continue
        try:
            if not isinstance(value, str):
                raise ValueError("адрес должен быть строкой")
            # getaddrinfo блокирующий: в отдельном потоке, чтобы не держать цикл событий
            await asyncio.to_thread(check_webhook_url, value, allow_private=allow_private)
        except ValueError as exc:
            raise _unprocessable(f"«{f.label}»: недопустимый адрес: {exc}") from None


async def _check_config(config: dict[str, Any], connector: Connector, *, allow_private: bool) -> None:
    _check_names(config, connector.config_fields, "настройки")
    _check_required(config, connector.config_fields, "настройки")
    await _check_urls(config, connector.config_fields, allow_private=allow_private)


async def _check_secrets(secrets: dict[str, Any], connector: Connector, *, allow_private: bool) -> None:
    _check_names(secrets, connector.secret_fields, "секреты")
    _check_required(secrets, connector.secret_fields, "секреты")
    await _check_urls(secrets, connector.secret_fields, allow_private=allow_private)


def _encrypt(secrets: dict[str, str], key: str) -> str:
    try:
        return encrypt_secrets(secrets, key)
    except SecretsKeyMissing as exc:
        raise _unprocessable(str(exc)) from None


def _decrypt(d: Destination, key: str) -> dict[str, Any]:
    if d.secrets_enc is None:
        return {}
    try:
        return decrypt_secrets(d.secrets_enc, key)
    except SecretsKeyMissing as exc:
        raise _unprocessable(str(exc)) from None


async def _owned(session, tenant_id, destination_id: str) -> Destination:
    d = await session.get(Destination, parse_id(destination_id))
    if d is None or d.tenant_id != tenant_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NOT_FOUND)
    return d


@catalog_router.get(
    "",
    summary="Типы получателей",
    description="Какие системы можно подключить и какие поля заполнить: настройки, секреты, целевые поля для маппинга.",
)
async def list_connectors(_: TenantDep) -> list[dict[str, Any]]:
    return [
        {
            "kind": c.kind,
            "label": c.label,
            "config_fields": [dataclasses.asdict(f) for f in c.config_fields],
            "secret_fields": [dataclasses.asdict(f) for f in c.secret_fields],
            "target_fields": [dataclasses.asdict(f) for f in c.target_fields],
        }
        for c in REGISTRY.values()
    ]


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Подключить систему",
    description=(
        "Создаёт получателя выгрузки. 422 — неизвестный тип, не заполнены обязательные поля, адрес ведёт на "
        "частную сеть или на сервере не настроен ключ шифрования (при непустых секретах). "
        "403 — сессия оператора (нужен владелец или ключ API). Секреты в ответе не возвращаются."
    ),
)
async def create_destination(
    body: DestinationIn, manager: ManagerDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    connector = _connector(body.kind)
    await _check_config(body.config, connector, allow_private=settings.webhook_allow_private)
    await _check_secrets(body.secrets, connector, allow_private=settings.webhook_allow_private)
    dest = Destination(
        tenant_id=manager.tenant_id,
        kind=body.kind,
        name=body.name,
        config=body.config,
        secrets_enc=_encrypt(body.secrets, settings.secrets_key) if body.secrets else None,
        mapping=body.mapping,
        active=True,
    )
    session.add(dest)
    await session.commit()
    return _public(dest)


@router.get("", summary="Список получателей", description="Все получатели тенанта. Секреты не возвращаются.")
async def list_destinations(tenant_id: TenantDep, session: SessionDep) -> list[dict[str, Any]]:
    rows = (
        await session.execute(
            select(Destination).where(Destination.tenant_id == tenant_id).order_by(Destination.created_at)
        )
    ).scalars()
    return [_public(d) for d in rows]


@router.get("/{destination_id}", summary="Получатель", description="Чужой или несуществующий — 404.")
async def get_destination(destination_id: str, tenant_id: TenantDep, session: SessionDep) -> dict[str, Any]:
    return _public(await _owned(session, tenant_id, destination_id))


@router.patch(
    "/{destination_id}",
    summary="Изменить получателя",
    description=(
        "Меняет только переданные поля. `config` и `mapping` заменяются целиком, `secrets` дополняют сохранённые: "
        "непереданные секреты остаются. Тип не меняется."
    ),
)
async def patch_destination(
    destination_id: str, body: DestinationPatch, manager: ManagerDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    dest = await _owned(session, manager.tenant_id, destination_id)
    connector = _connector(dest.kind)
    if body.name is not None:
        dest.name = body.name
    if body.config is not None:
        await _check_config(body.config, connector, allow_private=settings.webhook_allow_private)
        dest.config = body.config
    if body.secrets:
        _check_names(body.secrets, connector.secret_fields, "секреты")
        try:
            stored = _decrypt(dest, settings.secrets_key)
        except HTTPException:
            # ключ шифрования сменился/потерян: сохранённое не прочитать. Если переданные секреты закрывают
            # все обязательные поля, перешифровываем их текущим ключом — это путь восстановления
            try:
                _check_required(body.secrets, connector.secret_fields, "секреты")
            except HTTPException:
                raise _unprocessable(
                    "сохранённые секреты не расшифровываются (ключ шифрования изменился): "
                    "введите все обязательные секреты заново"
                ) from None
            stored = {}
        merged = {**stored, **body.secrets}
        await _check_secrets(merged, connector, allow_private=settings.webhook_allow_private)
        dest.secrets_enc = _encrypt(merged, settings.secrets_key)
    if body.mapping is not None:
        dest.mapping = body.mapping
    if body.active is not None:
        dest.active = body.active
    await session.commit()
    return _public(dest)


@router.delete(
    "/{destination_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Удалить получателя",
    description="Удаляет подключение; журнал выгрузок сохраняется. Правила, ссылающиеся на него, нужно поправить.",
)
async def delete_destination(destination_id: str, manager: ManagerDep, session: SessionDep) -> Response:
    dest = await _owned(session, manager.tenant_id, destination_id)
    # правила хранят id получателя внутри JSON: убираем его, чтобы они не ссылались на удалённого
    row = await session.get(RoutingRules, manager.tenant_id)
    if row is not None:
        gone = str(dest.id)
        pruned = [
            {
                **r,
                "export_to": [i for i in r.get("export_to", []) if i != gone],
                "notify": [i for i in r.get("notify", []) if i != gone],
            }
            for r in row.rules
        ]
        if pruned != row.rules:
            row.rules = pruned
            row.updated_at = datetime.now(UTC)
    await session.delete(dest)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post(
    "/{destination_id}/test",
    summary="Проверить подключение",
    description=(
        "Обращается к системе без записи данных. Всегда 200 `{ok, message}`: сбой подключения — `ok: false` с "
        "причиной. 422 — не настроен ключ шифрования; 403 — сессия оператора."
    ),
)
async def test_destination(
    destination_id: str, manager: ManagerDep, session: SessionDep, settings: SettingsDep
) -> dict[str, Any]:
    dest = await _owned(session, manager.tenant_id, destination_id)
    secrets = _decrypt(dest, settings.secrets_key)
    connector = REGISTRY.get(dest.kind)
    if connector is None:
        return {"ok": False, "message": f"тип получателя «{dest.kind}» больше не поддерживается"}
    try:
        await _check_config(dest.config or {}, connector, allow_private=settings.webhook_allow_private)
        await _check_urls(secrets, connector.secret_fields, allow_private=settings.webhook_allow_private)
    except HTTPException as exc:
        return {"ok": False, "message": str(exc.detail)}
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as http:
            ctx = DestCtx(config=dest.config or {}, secrets=secrets, mapping=dest.mapping or {}, http=http)
            message = await connector.test(ctx)
    except ConnectorError as exc:
        return {"ok": False, "message": exc.message}
    except Exception as exc:
        # текст исключения может содержать адрес с ключом или секрет: наружу — только тип
        return {"ok": False, "message": f"Не удалось проверить подключение ({type(exc).__name__})"}
    return {"ok": True, "message": message or "Подключение работает"}
