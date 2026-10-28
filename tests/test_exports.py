"""Выгрузки: постановка без дублей, доставка, ретраи, отключённый получатель, ключ шифрования, SSRF."""

import uuid
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select

from docflow.config import Settings
from docflow.connectors.base import ConnectorError
from docflow.db.models import Destination, Document, Export, Tenant
from docflow.exports import (
    DESTINATION_OFF,
    RETRY_DELAYS,
    STALE_NOTIFICATION,
    deliver_export,
    enqueue_exports,
    enqueue_routed,
    export_job_id,
    requeue_stale_exports,
)
from docflow.pipeline import SchemaResolver
from docflow.queue import DELIVER_EXPORT, ListEnqueuer
from docflow.routing import RoutingDecision
from docflow.secrets import NO_KEY, encrypt_secrets
from docflow.storage.blobs import LocalBlobStore, scan_key

KEY = Fernet.generate_key().decode()
URL = "https://93.184.216.34/api"
DATA = {"number": "15", "issued": "2026-10-20", "total": 250.0, "supplier": "ООО «Ромашка»"}


@pytest.fixture
def settings():
    return Settings(
        secrets_key=KEY,
        public_base_url="https://api.example.test",
        public_site_url="https://docflow.example.test",
        webhook_allow_private=False,
    )


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def setup(session, *, status="ok", data=None, issues=None):
    t = Tenant(name="t")
    session.add(t)
    await session.flush()
    doc = Document(
        tenant_id=t.id, schema_slug="invoice_ru", schema_version=1, status=status, files=[], mode="gray",
        data=DATA if data is None else data, issues=issues or [], pages=1,
    )
    session.add(doc)
    await session.commit()
    return t, doc


async def add_dest(session, tenant_id, *, kind="test_fake", active=True, config=None, secrets=None, key=KEY):
    secrets = {"token": "SUPER-SECRET"} if secrets is None else secrets
    d = Destination(
        tenant_id=tenant_id, kind=kind, name=f"Получатель {kind}", config=config or {"endpoint": URL},
        secrets_enc=encrypt_secrets(secrets, key) if secrets else None, mapping={"total": "amount"}, active=active,
    )
    session.add(d)
    await session.commit()
    return d


async def exports_of(session, document_id):
    q = select(Export).where(Export.document_id == document_id).execution_options(populate_existing=True)
    return (await session.execute(q)).scalars().all()


async def run(session, export_id, settings, blobs, enqueuer=None, expected_attempt=None):
    enqueuer = enqueuer or ListEnqueuer()
    async with httpx.AsyncClient() as http:
        await deliver_export(
            session, export_id, http=http, enqueuer=enqueuer, blobs=blobs, schemas=SchemaResolver(),
            settings=settings, expected_attempt=expected_attempt,
        )
    return await session.get(Export, export_id, populate_existing=True), enqueuer


# --- постановка -----------------------------------------------------------


async def test_enqueue_filters_and_is_idempotent(session, fake_connector, fake_notifier):
    t, doc = await setup(session)
    good = await add_dest(session, t.id)
    off = await add_dest(session, t.id, active=False)
    notifier = await add_dest(session, t.id, kind="test_notifier", secrets={})
    other_tenant = Tenant(name="чужой")
    session.add(other_tenant)
    await session.commit()
    foreign = await add_dest(session, other_tenant.id)
    dead = uuid.uuid4()
    enq = ListEnqueuer()

    ids = {good.id, off.id, notifier.id, foreign.id, dead}
    assert await enqueue_exports(session, doc, "export", ids, enq) == 1
    rows = await exports_of(session, doc.id)
    assert [(r.destination_id, r.purpose, r.status, r.attempt) for r in rows] == [(good.id, "export", "pending", 0)]
    assert rows[0].tenant_id == t.id
    assert enq.calls == [(DELIVER_EXPORT, (str(rows[0].id), 0), None, export_job_id(rows[0].id, 0))]

    # уведомлять умеют оба
    assert await enqueue_exports(session, doc, "notify_review", ids, enq) == 2

    # повтор — без новых строк и задач
    enq2 = ListEnqueuer()
    assert await enqueue_exports(session, doc, "export", ids, enq2) == 0
    assert await enqueue_exports(session, doc, "notify_review", ids, enq2) == 0
    assert enq2.calls == []
    assert len(await exports_of(session, doc.id)) == 3


async def test_done_export_is_never_resent(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    row, _ = await run(session, row.id, settings, blobs)
    assert row.status == "done"
    enq = ListEnqueuer()
    assert await enqueue_exports(session, doc, "export", {dest.id}, enq) == 0
    assert enq.calls == []
    # повторная доставка той же задачи (дубль в очереди) ничего не шлёт
    await run(session, row.id, settings, blobs)
    assert [c[0] for c in fake_connector.calls] == ["export"]


async def test_enqueue_routed_by_status(session, fake_connector):
    t, doc = await setup(session, status="ok")
    a = await add_dest(session, t.id)
    n = await add_dest(session, t.id)
    decision = RoutingDecision(export_to={a.id}, notify={n.id}, approval=[])
    assert await enqueue_routed(session, doc, decision, ListEnqueuer()) == 2
    assert sorted((r.destination_id == a.id, r.purpose) for r in await exports_of(session, doc.id)) == [
        (False, "notify_new"),
        (True, "export"),
    ]

    _, review = await setup(session, status="needs_review")
    a2 = await add_dest(session, review.tenant_id)
    n2 = await add_dest(session, review.tenant_id)
    decision = RoutingDecision(export_to={a2.id}, notify={n2.id}, approval=["x"])
    assert await enqueue_routed(session, review, decision, ListEnqueuer()) == 1
    assert [(r.destination_id, r.purpose) for r in await exports_of(session, review.id)] == [(n2.id, "notify_review")]

    for status in ("failed", "queued", "processing"):
        _, d = await setup(session, status=status)
        assert await enqueue_routed(session, d, decision, ListEnqueuer()) == 0


# --- доставка -------------------------------------------------------------


async def test_deliver_success_passes_doc_and_secrets(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await blobs.put(scan_key(str(doc.id)), b"%PDF-scan", "application/pdf")
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)

    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "done" and row.external_id == "ext-1" and row.attempt == 1
    assert row.last_error is None and row.next_attempt_at is None
    assert enq.calls == []
    assert fake_connector.calls == [("export", doc.id, {"token": "SUPER-SECRET"})]
    sent = fake_connector.docs[0]
    assert sent.id == doc.id and sent.tenant_id == t.id and sent.status == "ok"
    assert sent.schema.slug == "invoice_ru" and sent.title == sent.schema.title
    assert sent.number == "15" and sent.issued == date(2026, 10, 20)
    assert sent.data["total"] == 250.0
    assert sent.cabinet_url == f"https://docflow.example.test/app/documents/{doc.id}"
    assert sent.digital_pdf_url == f"https://api.example.test/v1/documents/{doc.id}/digital.pdf"
    assert sent.scan_pdf_url == f"https://api.example.test/v1/documents/{doc.id}/pdf"
    assert await sent.load_scan_pdf() == b"%PDF-scan"


async def test_scan_loader_missing_blob_is_none(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    await run(session, row.id, settings, blobs)
    assert await fake_connector.docs[0].load_scan_pdf() is None


async def test_notify_passes_purpose_and_issues(session, fake_connector, settings, blobs):
    issues = [{"field": "total", "code": "sum_mismatch", "message": "сумма не сходится"}]
    t, doc = await setup(session, status="needs_review", issues=issues)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "notify_review", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    row, _ = await run(session, row.id, settings, blobs)
    assert row.status == "done" and row.external_id is None
    assert fake_connector.calls == [("notify", doc.id, "notify_review")]
    assert fake_connector.docs[0].issues == issues


async def test_retryable_error_backoff_then_failed(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    fake_connector.export_errors = [ConnectorError("сервер занят (HTTP 503)", retryable=True) for _ in range(10)]

    for n, delay in enumerate(RETRY_DELAYS, start=1):
        before = datetime.now(UTC)
        row, enq = await run(session, row.id, settings, blobs)
        assert row.status == "pending" and row.attempt == n
        assert row.last_error == "сервер занят (HTTP 503)"
        assert row.next_attempt_at >= before + timedelta(seconds=delay - 1)
        assert enq.calls == [(DELIVER_EXPORT, (str(row.id), n), delay, export_job_id(row.id, n))]

    row, enq = await run(session, row.id, settings, blobs)  # шестая попытка — последняя
    assert row.status == "failed" and row.attempt == len(RETRY_DELAYS) + 1
    assert row.next_attempt_at is None and enq.calls == []
    assert len([c for c in fake_connector.calls if c[0] == "export"]) == 6


async def test_retry_after_error_succeeds(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    fake_connector.export_errors = [ConnectorError("таймаут", retryable=True)]
    row, _ = await run(session, row.id, settings, blobs)
    assert row.status == "pending"
    row, _ = await run(session, row.id, settings, blobs)
    assert row.status == "done" and row.last_error is None and row.attempt == 2


async def test_non_retryable_error_fails_at_once(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    fake_connector.export_errors = [ConnectorError("номенклатура «Цемент» не найдена в 1С", retryable=False)]
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and row.attempt == 1
    assert row.last_error == "номенклатура «Цемент» не найдена в 1С"
    assert enq.calls == []


async def test_unexpected_exception_is_retried_without_secret_text(session, fake_connector, settings, blobs, caplog):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    fake_connector.export_errors = [httpx.ConnectError("https://portal/rest/1/SUPER-SECRET/ недоступен")]
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "pending" and len(enq.calls) == 1
    assert "SUPER-SECRET" not in row.last_error and "ConnectError" in row.last_error
    assert "SUPER-SECRET" not in caplog.text


@pytest.mark.parametrize("how", ["inactive", "deleted"])
async def test_destination_off_between_enqueue_and_delivery(session, fake_connector, settings, blobs, how):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    if how == "inactive":
        dest.active = False
    else:
        await session.delete(dest)
    await session.commit()
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and row.last_error == DESTINATION_OFF and enq.calls == []
    assert fake_connector.calls == []


async def test_secrets_key_missing_fails_without_retry(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings.model_copy(update={"secrets_key": ""}), blobs)
    assert row.status == "failed" and row.last_error == NO_KEY and enq.calls == []
    assert fake_connector.calls == []


async def test_changed_key_fails_without_retry(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    other = settings.model_copy(update={"secrets_key": Fernet.generate_key().decode()})
    row, enq = await run(session, row.id, other, blobs)
    assert row.status == "failed" and "ключ шифрования" in row.last_error and enq.calls == []


@pytest.mark.parametrize(
    ("config", "secrets"),
    [
        ({"endpoint": "https://10.0.0.5/api"}, {"token": "x"}),
        ({"endpoint": URL}, {"token": "x", "hook": "http://127.0.0.1:8000/hook"}),
    ],
)
async def test_ssrf_rechecked_at_delivery(session, fake_connector, settings, blobs, config, secrets):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id, config=config, secrets=secrets)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    # частный адрес — ошибка настройки, а не сбой сети: сразу failed, без повторов
    assert row.status == "failed" and "не публичный" in row.last_error and enq.calls == []
    assert fake_connector.calls == []
    # в dev частные адреса разрешены
    row.status, row.attempt = "pending", 0
    await session.commit()
    row, _ = await run(session, row.id, settings.model_copy(update={"webhook_allow_private": True}), blobs)
    assert row.status == "done"


@pytest.mark.parametrize("status", ["needs_review", "failed"])
async def test_export_of_unapproved_document_never_sent(session, fake_connector, settings, blobs, status):
    """Анти-цель: needs_review/failed не уходит в учётную систему, даже если задача как-то появилась."""
    t, doc = await setup(session, status=status)
    dest = await add_dest(session, t.id)
    session.add(Export(tenant_id=t.id, document_id=doc.id, destination_id=dest.id, purpose="export"))
    await session.commit()
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and "не подтверждён" in row.last_error and enq.calls == []
    assert fake_connector.calls == []


async def test_unknown_connector_kind_fails(session, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id, kind="removed_kind", secrets={})
    session.add(Export(tenant_id=t.id, document_id=doc.id, destination_id=dest.id, purpose="export"))
    await session.commit()
    (row,) = await exports_of(session, doc.id)
    row, _ = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and "removed_kind" in row.last_error


async def test_missing_or_not_pending_is_noop(session, fake_connector, settings, blobs):
    await run(session, uuid.uuid4(), settings, blobs)
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    session.add(Export(tenant_id=t.id, document_id=doc.id, destination_id=dest.id, purpose="export", status="failed"))
    await session.commit()
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and row.attempt == 0 and enq.calls == [] and fake_connector.calls == []


# --- подбор зависших ------------------------------------------------------


async def test_requeue_stale_exports(session):
    t, doc = await setup(session)
    now = datetime.now(UTC)
    dests = [await add_dest(session, t.id, secrets={}) for _ in range(4)]
    lost = Export(tenant_id=t.id, document_id=doc.id, destination_id=dests[0].id, created_at=now - timedelta(hours=1))
    fresh = Export(tenant_id=t.id, document_id=doc.id, destination_id=dests[1].id, created_at=now)
    overdue = Export(
        tenant_id=t.id, document_id=doc.id, destination_id=dests[2].id, attempt=2,
        created_at=now - timedelta(hours=3), next_attempt_at=now - timedelta(hours=1),
    )
    done = Export(
        tenant_id=t.id, document_id=doc.id, destination_id=dests[3].id, status="done",
        created_at=now - timedelta(hours=3),
    )
    session.add_all([lost, fresh, overdue, done])
    await session.commit()
    enq = ListEnqueuer()
    assert await requeue_stale_exports(session, enq, now) == 2
    assert sorted(c[3] for c in enq.calls) == sorted([export_job_id(lost.id, 0), export_job_id(overdue.id, 2)])


async def test_in_flight_delivery_is_not_requeued(session, settings, blobs):
    """Пока коннектор работает (до таймаута задачи), крон не ставит вторую копию: дубль во внешней системе."""
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    seen = {}

    from docflow.connectors import base

    class Probe(base.BaseConnector):
        kind = "probe"
        label = "probe"

        async def export(self, ctx, d):
            seen["requeued"] = await requeue_stale_exports(session, ListEnqueuer(), datetime.now(UTC) + timedelta(
                minutes=6
            ))
            return "x"

    base.REGISTRY["probe"] = Probe()
    try:
        dest.kind = "probe"
        session.add(
            Export(tenant_id=t.id, document_id=doc.id, destination_id=dest.id, created_at=datetime.now(UTC)
                   - timedelta(hours=1))
        )
        await session.commit()
        (row,) = await exports_of(session, doc.id)
        row, _ = await run(session, row.id, settings, blobs)
    finally:
        base.REGISTRY.pop("probe", None)
    assert row.status == "done" and seen["requeued"] == 0


# --- доработки по ревью ---------------------------------------------------


async def test_attempt_cap_reached_without_result_fails(session, fake_connector, settings, blobs):
    """Попытка умерла вне try (таймаут arq, kill воркера): крон не должен слать во внешнюю систему вечно."""
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    session.add(
        Export(tenant_id=t.id, document_id=doc.id, destination_id=dest.id, attempt=len(RETRY_DELAYS) + 1,
               last_error="нет связи")
    )
    await session.commit()
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and enq.calls == [] and fake_connector.calls == []
    assert "превышено число попыток" in row.last_error and "нет связи" in row.last_error


async def test_connector_timeout_is_retried(session, fake_connector, settings, blobs, monkeypatch):
    import docflow.exports as exports_module

    monkeypatch.setattr(exports_module, "CONNECTOR_DEADLINE", 0.05)
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    fake_connector.export_delay = 2
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "pending" and row.attempt == 1
    assert row.last_error == "внешняя система не ответила вовремя"
    assert enq.calls == [(DELIVER_EXPORT, (str(row.id), 1), RETRY_DELAYS[0], export_job_id(row.id, 1))]


async def test_crash_after_claim_goes_through_retry(session, fake_connector, settings, blobs, monkeypatch):
    import docflow.exports as exports_module

    def boom(*a, **k):
        raise RuntimeError("секрет SUPER-SECRET в тексте")

    monkeypatch.setattr(exports_module, "build_export_doc", boom)
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "pending" and row.attempt == 1 and len(enq.calls) == 1
    assert "RuntimeError" in row.last_error and "SUPER-SECRET" not in row.last_error


@pytest.mark.parametrize(
    ("purpose", "status"), [("notify_review", "ok"), ("notify_new", "needs_review"), ("notify_review", "failed")]
)
async def test_stale_notification_not_sent(session, fake_connector, settings, blobs, purpose, status):
    t, doc = await setup(session, status=status)
    dest = await add_dest(session, t.id)
    session.add(Export(tenant_id=t.id, document_id=doc.id, destination_id=dest.id, purpose=purpose))
    await session.commit()
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and row.last_error == STALE_NOTIFICATION and enq.calls == []
    assert fake_connector.calls == []


async def test_stray_duplicate_job_is_ignored(session, fake_connector, settings, blobs):
    """Задача для попытки 1, а выгрузка уже на попытке 2 и под арендой/отложена: это дубль, не трогаем."""
    t, doc = await setup(session)
    dest = await add_dest(session, t.id)
    lease = datetime.now(UTC) + timedelta(minutes=10)
    session.add(Export(tenant_id=t.id, document_id=doc.id, destination_id=dest.id, attempt=2, next_attempt_at=lease))
    await session.commit()
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs, expected_attempt=1)
    assert row.status == "pending" and row.attempt == 2 and enq.calls == [] and fake_connector.calls == []
    # своя задача (ожидает попытку 2) — работает
    row, _ = await run(session, row.id, settings, blobs, expected_attempt=2)
    assert row.status == "done" and row.attempt == 3


async def test_dns_failure_is_retried(session, fake_connector, settings, blobs, monkeypatch):
    import socket

    def no_dns(*a, **k):
        raise OSError("Name or service not known")

    monkeypatch.setattr(socket, "getaddrinfo", no_dns)
    t, doc = await setup(session)
    dest = await add_dest(session, t.id, config={"endpoint": "https://portal.example/api"})
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "pending" and "не резолвится" in row.last_error and len(enq.calls) == 1
    assert fake_connector.calls == []


async def test_bad_scheme_fails_at_once(session, fake_connector, settings, blobs):
    t, doc = await setup(session)
    dest = await add_dest(session, t.id, config={"endpoint": "ftp://93.184.216.34/x"})
    await enqueue_exports(session, doc, "export", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and "схема" in row.last_error and enq.calls == []


async def test_email_host_ssrf_rechecked_at_delivery(session, settings, blobs):
    """Почта задаёт хост, а не URL: и его перед отправкой проверяем на публичность (без повторов)."""
    t, doc = await setup(session, status="ok")
    config = {"host": "127.0.0.1", "from_addr": "df@example.com", "to_addrs": "a@x.ru"}
    dest = await add_dest(session, t.id, kind="email", config=config, secrets={})
    await enqueue_exports(session, doc, "notify_new", {dest.id}, ListEnqueuer())
    (row,) = await exports_of(session, doc.id)
    row, enq = await run(session, row.id, settings, blobs)
    assert row.status == "failed" and "не публичный" in row.last_error and enq.calls == []
