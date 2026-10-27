"""Модели БД. Типы переносимые: Postgres в проде, SQLite в тестах."""

import hashlib
import secrets
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Явные имена ограничений: миграция и модели обязаны давать одинаковые имена
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(constraint_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# JSONB на Postgres (индексы и запросы по содержимому позже), на SQLite — обычный JSON
JsonType = JSON().with_variant(JSONB(), "postgresql")


class UtcDateTime(TypeDecorator):
    """DateTime(timezone=True), который и на SQLite отдаёт aware-значения в UTC."""

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime не допускается, нужен aware (UTC)")
        return value.astimezone(UTC)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _now() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def _id() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created() -> Mapped[datetime]:
    return mapped_column(UtcDateTime(), default=_now)


def hash_key(plain: str) -> str:
    return hashlib.sha256(plain.encode()).hexdigest()


def generate_api_key() -> tuple[str, str, str]:
    """(ключ целиком, префикс для показа, sha256). Сам ключ нигде не храним."""
    plain = "dfk_" + secrets.token_urlsafe(24)
    return plain, plain[:8], hash_key(plain)


class Tenant(Base):
    __tablename__ = "tenants"
    __table_args__ = (CheckConstraint("extractor IN ('claude', 'ollama')", name="extractor"),)

    id: Mapped[uuid.UUID] = _id()
    name: Mapped[str] = mapped_column(String)
    extractor: Mapped[str] = mapped_column(String, default="claude")
    created_at: Mapped[datetime] = _created()


class ApiKey(Base):
    __tablename__ = "api_keys"
    __table_args__ = (Index("ix_api_keys_key_hash", "key_hash", unique=True),)

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    prefix: Mapped[str] = mapped_column(String)
    key_hash: Mapped[str] = mapped_column(String)
    created_at: Mapped[datetime] = _created()
    revoked_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class SchemaRecord(Base):
    __tablename__ = "schemas"
    __table_args__ = (UniqueConstraint("tenant_id", "slug", "version", name="tenant_slug_version"),
        # NULL в UNIQUE считаются разными, поэтому встроенные схемы дедуплицируем отдельным индексом
        Index(
            "uq_schemas_builtin_slug_version",
            "slug",
            "version",
            unique=True,
            postgresql_where=text("tenant_id IS NULL"),
            sqlite_where=text("tenant_id IS NULL"),
        ),)

    id: Mapped[uuid.UUID] = _id()
    # None — встроенная схема, общая для всех
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), nullable=True)
    slug: Mapped[str] = mapped_column(String)
    version: Mapped[int] = mapped_column(Integer)
    body: Mapped[dict] = mapped_column(JsonType)
    created_at: Mapped[datetime] = _created()


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'processing', 'ok', 'needs_review', 'failed')", name="status"),
        UniqueConstraint("tenant_id", "idempotency_key", name="tenant_idempotency_key"),
        Index("ix_documents_tenant_id_created_at", "tenant_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="RESTRICT"))
    schema_slug: Mapped[str] = mapped_column(String)
    schema_version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default="queued")
    idempotency_key: Mapped[str | None] = mapped_column(String, nullable=True)
    files: Mapped[list] = mapped_column(JsonType)  # ключи blob-хранилища
    mode: Mapped[str] = mapped_column(String)
    data: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    issues: Mapped[list | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[str | None] = mapped_column(String, nullable=True)
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    pages: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = _created()
    # когда воркер взял документ в processing: по нему жнец находит задачи убитых воркеров
    claimed_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[uuid.UUID] = _id()
    tenant_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"))
    url: Mapped[str] = mapped_column(String)
    secret: Mapped[str] = mapped_column(String)
    events: Mapped[list] = mapped_column(JsonType)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = _created()


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'delivered', 'failed')", name="status"),
        UniqueConstraint("endpoint_id", "document_id", "event", name="endpoint_document_event"),
        Index("ix_webhook_deliveries_status_next_attempt_at", "status", "next_attempt_at"),
    )

    id: Mapped[uuid.UUID] = _id()
    endpoint_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("webhook_endpoints.id", ondelete="CASCADE"))
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    event: Mapped[str] = mapped_column(String)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String, default="pending")
    next_attempt_at: Mapped[datetime | None] = mapped_column(UtcDateTime(), nullable=True)
    last_error: Mapped[str | None] = mapped_column(String, nullable=True)
    response_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = _created()
