import uuid
from datetime import UTC, datetime, timedelta

from api_helpers import session_for
from sqlalchemy import select
from test_documents import add_doc
from test_webhooks_api import create as create_webhook

from docflow.db.models import ApiKey, Document, SchemaRecord, hash_key

SCHEMA = {
    "slug": "order_test",
    "version": 1,
    "title": "Наряд",
    "description": "тест",
    "fields": [
        {"name": "issued", "type": "date"},
        {"name": "delivery", "type": "date"},
        {"name": "total", "type": "number"},
    ],
    "rules": [{"kind": "date_order", "params": {"earlier": "issued", "later": "delivery"}}],
}


async def add_review_doc(api, headers, **values):
    """needs_review-документ со схемой тенанта order_test."""
    async with api.sessionmaker() as s:
        tenant_id = (
            await s.execute(select(ApiKey.tenant_id).where(ApiKey.key_hash == hash_key(headers["Authorization"][7:])))
        ).scalar_one()
        exists = (await s.execute(select(SchemaRecord.id).where(SchemaRecord.tenant_id == tenant_id))).first()
        if not exists:
            s.add(SchemaRecord(tenant_id=tenant_id, slug="order_test", version=1, body=SCHEMA))
            await s.commit()
    values.setdefault("status", "needs_review")
    values.setdefault("data", {"issued": "2026-10-20", "delivery": "2026-10-22", "total": 100.0})
    values.setdefault("issues", [{"field": "total", "code": "uncertain", "message": "не уверен"}])
    return await add_doc(api, headers, schema_slug="order_test", **values)


async def review(api, headers, doc_id, **body):
    body.setdefault("decision", "approve")
    return await api.client.post(f"/v1/documents/{doc_id}/review", headers=headers, json=body)


async def test_review_approve_sets_ok_and_webhook(api, auth):
    await create_webhook(api, auth)
    doc_id = await add_review_doc(api, auth)
    r = await review(api, auth, doc_id, note="проверил")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok"
    assert body["review"]["note"] == "проверил" and body["review"]["at"]
    assert body["data"]["total"] == 100.0  # без data правок нет
    assert [i["code"] for i in body["issues"]] == ["uncertain"]
    assert any(c[0] == "deliver_webhook" for c in api.enqueuer.calls)


async def test_review_reject_sets_failed_with_note(api, auth):
    await create_webhook(api, auth, events=["document.failed"])
    doc_id = await add_review_doc(api, auth)
    r = await review(api, auth, doc_id, decision="reject", note="нечитаемо")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "failed"
    assert r.json()["error"] == "отклонён при проверке: нечитаемо"
    assert any(c[0] == "deliver_webhook" for c in api.enqueuer.calls)
    doc2 = await add_review_doc(api, auth)
    r = await review(api, auth, doc2, decision="reject")
    assert r.json()["error"] == "отклонён при проверке"


async def test_review_twice_conflict(api, auth):
    doc_id = await add_review_doc(api, auth)
    assert (await review(api, auth, doc_id)).status_code == 200
    r = await review(api, auth, doc_id)
    assert r.status_code == 409
    assert r.json() == {"detail": "документ уже проверен или ещё обрабатывается"}


async def test_review_not_needs_review_409(api, auth):
    for st in ("ok", "failed", "queued", "processing"):
        doc_id = await add_review_doc(api, auth, status=st)
        assert (await review(api, auth, doc_id)).status_code == 409


async def test_review_approve_normalizes_edits(api, auth):
    doc_id = await add_review_doc(api, auth, issues=[])
    r = await review(api, auth, doc_id, data={"issued": "2026-10-20", "total": "12 500,50", "delivery": "24.10"})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "ok"
    assert r.json()["data"] == {"issued": "2026-10-20", "delivery": "2026-10-24", "total": 12500.5}
    assert r.json()["issues"] == []


async def test_review_reports_remaining_issues(api, auth):
    doc_id = await add_review_doc(api, auth)
    r = await review(api, auth, doc_id, data={"issued": "2026-10-20", "delivery": "2026-10-10", "total": 5})
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert [i["code"] for i in r.json()["issues"]] == ["date_order"]


async def test_review_foreign_404(api, auth, make_tenant):
    doc_id = await add_review_doc(api, auth)
    other = await make_tenant("Чужой")
    assert (await review(api, other, doc_id)).status_code == 404
    assert (await review(api, auth, str(uuid.uuid4()))).status_code == 404
    assert (await review(api, auth, "не-id")).status_code == 404


async def test_review_validates_body(api, auth):
    doc_id = await add_review_doc(api, auth)
    assert (await review(api, auth, doc_id, decision="maybe")).status_code == 422
    assert (await review(api, auth, doc_id, note="x" * 1001)).status_code == 422


async def test_review_records_user(api, auth):
    headers, user_id = await session_for(api, auth, role="operator")
    doc_id = await add_review_doc(api, auth)
    assert (await review(api, headers, doc_id)).status_code == 200
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(doc_id))
    assert doc.reviewed_by == user_id and doc.reviewed_at is not None
    # ключ API проверяет без пользователя
    doc2 = await add_review_doc(api, auth)
    assert (await review(api, auth, doc2)).status_code == 200
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(doc2))
    assert doc.reviewed_by is None and doc.reviewed_at is not None


async def test_unreviewed_payload_has_null_review(api, auth):
    doc_id = await add_doc(api, auth, status="ok")
    assert (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).json()["review"] is None


async def test_stats_reviewed_counts(api, auth):
    now = datetime.now(UTC)
    await add_doc(api, auth, status="ok")
    await add_doc(api, auth, status="ok", reviewed_at=now)
    await add_doc(api, auth, status="ok", reviewed_at=now - timedelta(days=20), created_at=now - timedelta(days=21))
    await add_doc(api, auth, status="failed", reviewed_at=now)
    await add_doc(api, auth, status="needs_review")
    body = (await api.client.get("/v1/stats", headers=auth, params={"days": 5})).json()
    assert body["reviewed"] == 2
    # без человека — только ok без reviewed_at: 1 из 4 завершённых
    assert body["auto_share"] == 0.25
