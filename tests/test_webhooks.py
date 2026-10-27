import hmac
import json
from datetime import UTC, datetime
from hashlib import sha256

import httpx
import pytest
import respx
from sqlalchemy import select

from docflow.db.models import Document, Tenant, WebhookDelivery, WebhookEndpoint
from docflow.queue import DELIVER_WEBHOOK, ArqEnqueuer, ListEnqueuer
from docflow.serializers import document_payload
from docflow.webhooks import RETRY_DELAYS, deliver, enqueue_webhooks, sign

URL = "https://client.example/hook"


async def _setup(session, status="ok", events=("document.completed",), active=True):
    t = Tenant(name="t")
    session.add(t)
    await session.flush()
    doc = Document(
        tenant_id=t.id, schema_slug="invoice", schema_version=1, status=status, files=["a"], mode="vision",
        data={"total": 5}, issues=[], pages=1, model="m", finished_at=datetime.now(UTC),
    )
    ep = WebhookEndpoint(tenant_id=t.id, url=URL, secret="s3cret", events=list(events), active=active)
    session.add_all([doc, ep])
    await session.commit()
    return doc, ep


def test_sign_matches_hmac():
    body = b'{"a":1}'
    assert sign("k", body) == "sha256=" + hmac.new(b"k", body, sha256).hexdigest()


def test_payload_shape_and_pdf_url():
    d = Document(
        id=__import__("uuid").uuid4(), schema_slug="invoice", schema_version=2, status="needs_review",
        data={}, issues=[{"field": "x", "code": "c", "message": "m"}], pages=2, model="m",
        created_at=datetime(2026, 1, 1, tzinfo=UTC), finished_at=None,
    )
    p = document_payload(d, "http://h/")
    assert p["schema"] == {"slug": "invoice", "version": 2}
    assert p["pdf_url"] == f"http://h/v1/documents/{d.id}/pdf"
    assert p["created_at"] == "2026-01-01T00:00:00+00:00" and p["finished_at"] is None
    d.status = "failed"
    assert document_payload(d, "http://h")["pdf_url"] is None


async def test_arq_enqueuer_and_list():
    calls = []

    class Pool:
        async def enqueue_job(self, job, *args, _defer_by=None):
            calls.append((job, args, _defer_by))

    from datetime import timedelta

    await ArqEnqueuer(Pool()).enqueue("j", 1, defer_by=10)
    await ArqEnqueuer(Pool()).enqueue("j", 2)
    assert calls == [("j", (1,), timedelta(seconds=10)), ("j", (2,), None)]
    le = ListEnqueuer()
    await le.enqueue("j", 1, defer_by=5)
    assert le.calls == [("j", (1,), 5)]


async def test_enqueue_idempotent_and_filters(session):
    doc, ep = await _setup(session)
    other = WebhookEndpoint(tenant_id=doc.tenant_id, url=URL, secret="x", events=["document.failed"])
    off = WebhookEndpoint(tenant_id=doc.tenant_id, url=URL, secret="x", events=["document.completed"], active=False)
    session.add_all([other, off])
    await session.commit()
    q = ListEnqueuer()
    assert await enqueue_webhooks(session, doc, q) == 1
    assert await enqueue_webhooks(session, doc, q) == 0
    assert len(q.calls) == 1 and q.calls[0][0] == DELIVER_WEBHOOK


async def test_enqueue_event_by_status(session):
    doc, _ = await _setup(session, status="failed", events=("document.failed",))
    assert await enqueue_webhooks(session, doc, ListEnqueuer()) == 1
    d = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert d.event == "document.failed"
    doc.status = "processing"
    with pytest.raises(ValueError):
        await enqueue_webhooks(session, doc, ListEnqueuer())


async def _delivery(session, **kw):
    doc, ep = await _setup(session, **kw)
    q = ListEnqueuer()
    await enqueue_webhooks(session, doc, q)
    d = (await session.execute(select(WebhookDelivery))).scalar_one()
    return doc, ep, d, ListEnqueuer()


@respx.mock
async def test_deliver_ok_signed_exact_body(session):
    doc, ep, d, q = await _delivery(session)
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as http:
        await deliver(session, d.id, http, q)
    req = route.calls.last.request
    assert req.headers["X-DocFlow-Signature"] == sign("s3cret", req.content)
    assert req.headers["X-DocFlow-Event"] == "document.completed"
    assert req.headers["X-DocFlow-Delivery"] == str(d.id)
    assert req.headers["content-type"] == "application/json"
    body = json.loads(req.content)
    assert body["event"] == "document.completed" and body["document"]["id"] == str(doc.id)
    await session.refresh(d)
    assert d.status == "delivered" and d.response_code == 200 and q.calls == []
    # повторный запуск — no-op
    async with httpx.AsyncClient() as http:
        await deliver(session, d.id, http, q)
    assert route.call_count == 1


@respx.mock
async def test_deliver_500_retries(session):
    _, _, d, q = await _delivery(session)
    respx.post(URL).mock(return_value=httpx.Response(500, text="x" * 2000))
    async with httpx.AsyncClient() as http:
        await deliver(session, d.id, http, q)
    await session.refresh(d)
    assert d.attempt == 1 and d.status == "pending" and d.response_code == 500
    assert d.next_attempt_at is not None and len(d.last_error) <= 500
    assert q.calls == [(DELIVER_WEBHOOK, (d.id,), 10)]


@respx.mock
async def test_deliver_network_error_counts(session):
    _, _, d, q = await _delivery(session)
    respx.post(URL).mock(side_effect=httpx.ConnectError("boom"))
    async with httpx.AsyncClient() as http:
        await deliver(session, d.id, http, q)
    await session.refresh(d)
    assert d.attempt == 1 and d.response_code is None and "boom" in d.last_error


@respx.mock
async def test_fifth_failure_marks_failed(session):
    _, _, d, q = await _delivery(session)
    respx.post(URL).mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as http:
        for _ in range(len(RETRY_DELAYS)):
            await deliver(session, d.id, http, q)
    await session.refresh(d)
    assert d.status == "failed" and d.attempt == 5
    assert [c[2] for c in q.calls] == [10, 60, 300, 1800]


@respx.mock
async def test_inactive_endpoint_fails(session):
    _, ep, d, q = await _delivery(session)
    ep.active = False
    await session.commit()
    route = respx.post(URL).mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as http:
        await deliver(session, d.id, http, q)
    await session.refresh(d)
    assert d.status == "failed" and d.last_error and route.call_count == 0
