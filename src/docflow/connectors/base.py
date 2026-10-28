"""Основа коннекторов: описание типа получателя, контекст вызова, ошибки, реестр."""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal, Protocol

import httpx

from docflow.schemas.model import DocSchema

FieldKind = Literal["text", "url", "number", "textarea", "select"]


class ConnectorError(Exception):
    """Сбой доставки. message — по-русски и без секретов; retryable — стоит ли повторять."""

    def __init__(self, message: str, retryable: bool = False):
        super().__init__(message)
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class FieldDef:
    """Поле формы получателя: описание для кабинета и для проверки на сервере."""

    name: str
    label: str
    required: bool = False
    help: str = ""
    type: FieldKind = "text"
    options: list[str] | None = None


Loader = Callable[[], Awaitable[bytes | None]]


@dataclass
class ExportDoc:
    """Документ в виде, удобном коннектору: данные уже нормализованы, ссылки готовы."""

    id: uuid.UUID
    tenant_id: uuid.UUID
    schema: DocSchema
    data: dict[str, Any]
    status: str
    created_at: datetime
    title: str = ""
    number: str | None = None
    issued: date | None = None
    cabinet_url: str = ""
    digital_pdf_url: str | None = None
    scan_pdf_url: str | None = None
    digital_loader: Loader | None = field(default=None, repr=False)
    scan_loader: Loader | None = field(default=None, repr=False)

    async def load_digital_pdf(self) -> bytes | None:
        return await self.digital_loader() if self.digital_loader else None

    async def load_scan_pdf(self) -> bytes | None:
        return await self.scan_loader() if self.scan_loader else None


@dataclass
class DestCtx:
    config: dict[str, Any]
    secrets: dict[str, Any]
    mapping: dict[str, str]
    http: httpx.AsyncClient


class Connector(Protocol):
    kind: str
    label: str
    config_fields: list[FieldDef]
    secret_fields: list[FieldDef]
    target_fields: list[FieldDef]

    async def test(self, ctx: DestCtx) -> str | None:
        """Проверка подключения без записи данных. Сообщение для пользователя или None."""

    async def export(self, ctx: DestCtx, doc: ExportDoc) -> str | None:
        """Выгружает документ, возвращает ID созданной сущности во внешней системе."""

    async def notify(self, ctx: DestCtx, doc: ExportDoc, purpose: str) -> None:
        """Уведомление (purpose: notify_review | notify_new)."""


class BaseConnector:
    """Значения по умолчанию: коннектор переопределяет только то, что умеет."""

    kind = ""
    label = ""
    config_fields: list[FieldDef] = []
    secret_fields: list[FieldDef] = []
    target_fields: list[FieldDef] = []

    async def test(self, ctx: DestCtx) -> str | None:
        return None

    async def export(self, ctx: DestCtx, doc: ExportDoc) -> str | None:
        raise ConnectorError(f"«{self.label}» не умеет выгружать документы")

    async def notify(self, ctx: DestCtx, doc: ExportDoc, purpose: str) -> None:
        raise ConnectorError(f"«{self.label}» не умеет отправлять уведомления")


REGISTRY: dict[str, Connector] = {}


def get_connector(kind: str) -> Connector:
    try:
        return REGISTRY[kind]
    except KeyError:
        raise ConnectorError(f"неизвестный тип получателя «{kind}»") from None
