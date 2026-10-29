"""Демо-тенант за одну команду: создание, прогон сценариев через настоящий конвейер, сброс.

Подменяется только модель (DemoExtractor); статусы, выгрузки и уведомления ставит продукт. Назад сдвигается
лишь время создания, обработки и проверки документа — выгрузки и письма уходят «сейчас».
"""
import asyncio
import json
import random
import secrets
import uuid
import zlib
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import async_sessionmaker

from docflow.api.deps import Principal
from docflow.api.dictionaries import DictionaryIn
from docflow.auth import hash_password
from docflow.config import Settings
from docflow.connectors.base import REGISTRY
from docflow.connectors.checks import InvalidSettings, check_config, check_secrets
from docflow.db.models import (
    ApiKey,
    Destination,
    Dictionary,
    Document,
    RoutingRules,
    SchemaRecord,
    Tenant,
    User,
    generate_api_key,
)
from docflow.demo.data import (
    ACT_SCHEMA,
    DEMO_TENANT_NAME,
    RULES_TEMPLATE,
    SEED,
    SUPPLIERS_DICT,
    DemoDestinations,
    DemoExtractor,
    Scenario,
    scenarios,
)
from docflow.demo.render import render_photo
from docflow.pdf.build import OcrEngine
from docflow.pipeline import Deps, SchemaResolver, process_document
from docflow.queue import Enqueuer
from docflow.review import review_document, route_approved
from docflow.routing_model import RulesIn
from docflow.schemas.model import DocSchema
from docflow.secrets import NO_KEY, SecretsKeyMissing, encrypt_secrets
from docflow.storage.blobs import BlobStore, original_key, page_key, scan_key
from docflow.webhooks import EVENT_REVIEWED, enqueue_webhooks

MSK = timezone(timedelta(hours=3))  # без перехода на летнее время с 2014 года
MAIL_FROM = "docflow@severnyput.example"
MAIL_ACCOUNTING = "buh@severnyput.example"
MAIL_OPERATOR = "operator@severnyput.example"


class DemoExists(Exception):
    """Демо-тенант уже есть, а сброс не запрошен."""


class DemoError(Exception):
    """Демо не создать; текст — для оператора."""


@dataclass
class SeedOptions:
    reset: bool = False
    today: date | None = None
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    google_key: dict | None = None
    google_sheet: str | None = None
    bitrix_webhook: str | None = None
    telegram_token: str | None = None
    telegram_chat: str | None = None
    owner_email: str = "demo@severnyput.example"
    operator_email: str = "operator@severnyput.example"


@dataclass
class SeedResult:
    tenant_id: uuid.UUID
    owner_email: str
    owner_password: str
    operator_email: str
    operator_password: str
    api_key: str
    counts: dict[str, int] = field(default_factory=dict)


@dataclass
class _Dest:
    role: str  # поле DemoDestinations
    kind: str
    name: str
    config: dict[str, Any]
    secrets: dict[str, str]


def _wanted_destinations(opts: SeedOptions) -> list[_Dest]:
    mail = {"host": opts.smtp_host, "port": opts.smtp_port, "security": "none", "from_addr": MAIL_FROM}
    out = [
        _Dest("mail_accounting", "email", "Почта бухгалтерии", {**mail, "to_addrs": MAIL_ACCOUNTING}, {}),
        _Dest("mail_operator", "email", "Почта оператора", {**mail, "to_addrs": MAIL_OPERATOR}, {}),
    ]
    if (opts.google_key is None) != (opts.google_sheet is None):
        raise DemoError("Google Таблица: нужны оба флага, --google-key и --google-sheet")
    if opts.google_key is not None:
        out.append(_Dest("sheets", "google_sheets", "Google Таблица", {"spreadsheet_id": opts.google_sheet},
                         {"service_account_json": json.dumps(opts.google_key, ensure_ascii=False)}))
    if opts.bitrix_webhook:
        out.append(_Dest("bitrix", "bitrix24", "Битрикс24", {}, {"webhook_url": opts.bitrix_webhook}))
    if bool(opts.telegram_token) != bool(opts.telegram_chat):
        raise DemoError("Telegram: нужны оба флага, --telegram-token и --telegram-chat")
    if opts.telegram_token:
        out.append(_Dest("telegram", "telegram", "Telegram", {"chat_id": opts.telegram_chat},
                         {"bot_token": opts.telegram_token}))
    return out


async def _prepare_destinations(opts: SeedOptions, settings: Settings) -> list[tuple[_Dest, str | None]]:
    """Все проверки подключений — до создания чего-либо: те же, что у API. Возвращает (подключение, шифр)."""
    out = []
    for d in _wanted_destinations(opts):
        connector = REGISTRY[d.kind]
        try:
            await check_config(d.config, connector, allow_private=settings.webhook_allow_private)
            await check_secrets(d.secrets, connector, allow_private=settings.webhook_allow_private)
        except InvalidSettings as exc:
            raise DemoError(f"{d.name}: {exc}") from None
        if d.secrets and not settings.secrets_key:
            raise DemoError(f"{d.name}: {NO_KEY}")
        try:
            enc = encrypt_secrets(d.secrets, settings.secrets_key) if d.secrets else None
        except SecretsKeyMissing as exc:
            raise DemoError(f"{d.name}: {exc}") from None
        out.append((d, enc))
    return out


async def _demo_ids(session) -> list[uuid.UUID]:
    return list((await session.execute(select(Tenant.id).where(Tenant.name == DEMO_TENANT_NAME))).scalars())


async def reset_demo(sm: async_sessionmaker, blobs: BlobStore) -> bool:
    """Удаляет тенант с именем ровно DEMO_TENANT_NAME: сначала файлы документов, затем строки. Был ли он."""
    async with sm() as session:
        ids = await _demo_ids(session)
        if not ids:
            return False
        docs = (
            await session.execute(
                select(Document.id, Document.files, Document.pages).where(Document.tenant_id.in_(ids))
            )
        ).all()
        for doc_id, files, pages in docs:
            for key in [*(files or []), scan_key(str(doc_id))]:
                await blobs.delete(key)
            # pages пуст у упавших документов, а превью конвейер успевает записать до модели
            n = 1
            while n <= max(pages or 0, len(files or [])) or await blobs.exists(page_key(str(doc_id), n)):
                await blobs.delete(page_key(str(doc_id), n))
                n += 1
        # выгрузки и доставки вебхуков уходят каскадом от документов, остальное — от тенанта
        await session.execute(delete(Document).where(Document.tenant_id.in_(ids)))
        await session.execute(delete(Tenant).where(Tenant.id.in_(ids)))
        await session.commit()
    return True


def _scenario_seed(sc: Scenario) -> int:
    return SEED ^ zlib.crc32(sc.key.encode())


def _timeline(items: list[Scenario], today: date, now: datetime) -> list[tuple[Scenario, datetime]]:
    """Сценарии от старых к новым с моментом создания; «сегодня позже текущего часа» прижимается к now."""
    natural = []
    for sc in items:
        minute = random.Random(_scenario_seed(sc)).randint(0, 59)
        natural.append((datetime.combine(today - timedelta(days=sc.days_ago), time(sc.hour, minute), MSK), sc))
    natural.sort(key=lambda p: p[0])
    n = len(natural)
    # min двух возрастающих последовательностей возрастает: порядок не ломается
    return [(sc, min(at, now - timedelta(minutes=10 * (n - i)))) for i, (at, sc) in enumerate(natural)]


async def _create_tenant(
    sm: async_sessionmaker, opts: SeedOptions, dests: list[tuple[_Dest, str | None]]
) -> tuple[SeedResult, uuid.UUID, DemoDestinations]:
    owner_password, operator_password = secrets.token_urlsafe(12), secrets.token_urlsafe(12)
    plain, prefix, key_hash = generate_api_key()
    async with sm() as session:
        tenant = Tenant(name=DEMO_TENANT_NAME, extractor="ollama", auto_ok=True)
        session.add(tenant)
        await session.flush()
        tid = tenant.id
        session.add(ApiKey(tenant_id=tid, prefix=prefix, key_hash=key_hash))
        session.add(User(tenant_id=tid, email=opts.owner_email, password_hash=hash_password(owner_password),
                         role="owner"))
        operator = User(tenant_id=tid, email=opts.operator_email, password_hash=hash_password(operator_password),
                        role="operator")
        session.add(operator)
        act = DocSchema.model_validate(ACT_SCHEMA)
        session.add(SchemaRecord(tenant_id=tid, slug=act.slug, version=act.version, body=act.model_dump(mode="json")))
        suppliers = DictionaryIn.model_validate(SUPPLIERS_DICT)
        session.add(Dictionary(tenant_id=tid, name="suppliers", title=suppliers.title,
                               entries=[e.model_dump() for e in suppliers.entries]))
        ids: dict[str, uuid.UUID] = {}
        for d, enc in dests:
            row = Destination(tenant_id=tid, kind=d.kind, name=d.name, config=d.config, secrets_enc=enc,
                              mapping={}, active=True)
            session.add(row)
            await session.flush()
            ids[d.role] = row.id
        demo_dest = DemoDestinations(**ids)
        rules = RulesIn.model_validate({"rules": RULES_TEMPLATE(demo_dest)}).rules
        session.add(RoutingRules(tenant_id=tid, rules=[r.model_dump(mode="json", by_alias=True) for r in rules],
                                 updated_at=datetime.now(UTC)))
        await session.flush()
        operator_id = operator.id
        await session.commit()
    result = SeedResult(tid, opts.owner_email, owner_password, opts.operator_email, operator_password, plain)
    return result, operator_id, demo_dest


async def _run_scenario(
    sm: async_sessionmaker,
    deps: Deps,
    tenant_id: uuid.UUID,
    principal: Principal,
    sc: Scenario,
    created: datetime,
    now: datetime,
) -> None:
    resolver = deps.schemas
    async with sm() as session:
        schema = await resolver.resolve(session, tenant_id, sc.schema, None)
    seed = _scenario_seed(sc)
    doc_id = uuid.uuid4()
    key = original_key(str(doc_id), 1, "photo.jpg")
    jpeg = await asyncio.to_thread(render_photo, sc, schema.title, seed=seed)
    # файл — до строки: queued-документ без файла жнец/перепостановка не должны увидеть
    await deps.blobs.put(key, jpeg, "image/jpeg")
    async with sm() as session:
        session.add(Document(id=doc_id, tenant_id=tenant_id, schema_slug=schema.slug, schema_version=schema.version,
                             status="queued", files=[key], mode="gray", created_at=created))
        await session.commit()

    await process_document(replace(deps, extractors={"ollama": DemoExtractor(sc.answer)}), doc_id)

    async with sm() as session:
        status = await session.scalar(select(Document.status).where(Document.id == doc_id))
        if status != sc.expect:
            raise DemoError(f"сценарий «{sc.key}»: ожидался статус {sc.expect}, получен {status}")
        if sc.review is not None:
            approve = sc.review != "reject"
            data = {**sc.answer, **(sc.fix or {})} if sc.review == "approve_edit" else None
            document = await review_document(
                session, document_id=doc_id, tenant_id=tenant_id, principal=principal,
                decision="approve" if approve else "reject", data=data, note=sc.review_note, schemas=resolver,
            )
            # как в API: вебхуки, затем выгрузки по подтверждённым данным
            await enqueue_webhooks(session, document, deps.enqueuer, event=EVENT_REVIEWED if approve else None)
            if approve:
                await route_approved(session, document, resolver, deps.enqueuer)

        rng = random.Random(seed)
        values: dict[str, Any] = {
            "claimed_at": created + timedelta(seconds=rng.randint(5, 50)),
            "finished_at": created + timedelta(seconds=rng.randint(60, 180)),
        }
        if sc.review is not None:
            real = await session.scalar(select(Document.reviewed_at).where(Document.id == doc_id))
            # проверка «через 2–6 ч», но не позже, чем она прошла на самом деле
            values["reviewed_at"] = min(created + timedelta(minutes=rng.randint(120, 360)), real or now)
        await session.execute(update(Document).where(Document.id == doc_id).values(**values))
        await session.commit()


async def seed_demo(
    sm: async_sessionmaker,
    blobs: BlobStore,
    ocr: OcrEngine,
    enqueuer: Enqueuer,
    settings: Settings,
    opts: SeedOptions,
) -> SeedResult:
    dests = await _prepare_destinations(opts, settings)
    async with sm() as session:
        exists = bool(await _demo_ids(session))
    if exists and not opts.reset:
        raise DemoExists(DEMO_TENANT_NAME)
    if exists:
        await reset_demo(sm, blobs)
    async with sm() as session:
        taken = (
            await session.execute(select(User.email).where(User.email.in_([opts.owner_email, opts.operator_email])))
        ).scalars().all()
    if taken:
        raise DemoError(f"пользователь {', '.join(sorted(taken))} уже есть в другом тенанте")

    result, operator_id, _ = await _create_tenant(sm, opts, dests)
    principal = Principal(result.tenant_id, user_id=operator_id, role="operator")
    deps = Deps(sessionmaker=sm, blobs=blobs, ocr=ocr, extractors={}, schemas=SchemaResolver(), enqueuer=enqueuer,
                max_pages=settings.max_pages)
    now = datetime.now(MSK)
    today = opts.today or now.date()
    for sc, created in _timeline(scenarios(today), today, now):
        await _run_scenario(sm, deps, result.tenant_id, principal, sc, created, now)

    async with sm() as session:
        rows = await session.execute(
            select(Document.status, func.count())
            .where(Document.tenant_id == result.tenant_id)
            .group_by(Document.status)
        )
        result.counts = {status: n for status, n in rows.all()}
    return result
