"""schema=auto: загрузка, безопасность для экранов и выгрузок, «Распознать заново» с выбранным типом."""

import uuid
from datetime import date

import pytest
from sqlalchemy import select
from test_documents import add_doc, set_status, upload
from test_usage_api import LIMIT_TEXT

from docflow.db.models import BotChat, Destination, Document, Export, TenantBot
from docflow.intake import ENQUEUE_ERROR
from docflow.queue import PROCESS_DOCUMENT
from docflow.schemas.library import builtin_schemas

CONFLICT = "Выбрать тип можно только у документа, тип которого не определён"


async def get_doc(api, doc_id: str) -> Document:
    async with api.sessionmaker() as s:
        return await s.get(Document, uuid.UUID(doc_id), populate_existing=True)


async def failed_auto(api, auth, **values) -> str:
    values = {"schema_slug": "auto", "status": "failed", "error": "Не удалось определить тип документа", **values}
    files = values.pop("files", ["originals/x/1.png"])
    doc_id = await add_doc(api, auth, **values)
    await set_status(api, doc_id, files=files)  # add_doc сам задаёт files=[]
    return doc_id


def retype(api, headers, doc_id, schema="invoice_ru"):
    return api.client.post(f"/v1/documents/{doc_id}/retype", headers=headers, json={"schema": schema})


# --- загрузка ---------------------------------------------------------------


async def test_upload_auto_queued(api, auth):
    r = await upload(api, auth, schema="auto", schema_version="7")
    assert r.status_code == 202, r.text
    doc_id = r.json()["id"]
    assert r.json()["status"] == "queued"
    got = await api.client.get(f"/v1/documents/{doc_id}", headers=auth)
    assert got.json()["schema"] == {"slug": "auto", "version": 0}
    assert api.enqueuer.calls[0][:2] == (PROCESS_DOCUMENT, (doc_id,))


# --- auto не ломает экраны и выгрузки ------------------------------------------


@pytest.mark.parametrize("doc_status", ["queued", "processing", "failed"])
async def test_auto_safe_everywhere(api, auth, doc_status):
    doc_id = await add_doc(api, auth, schema_slug="auto", status=doc_status, error=None)

    listing = await api.client.get("/v1/documents", headers=auth)
    assert listing.status_code == 200
    (item,) = [i for i in listing.json()["items"] if i["id"] == doc_id]
    assert item["issue"] is None
    assert (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).status_code == 200
    assert (await api.client.get(f"/v1/documents/{doc_id}/pdf", headers=auth)).status_code == 409
    assert (await api.client.get(f"/v1/documents/{doc_id}/digital.pdf", headers=auth)).status_code == 409
    assert (await api.client.get("/v1/stats", headers=auth)).status_code == 200
    assert (await api.client.get("/v1/exports", headers=auth)).status_code == 200
    today = date.today().isoformat()
    r = await api.client.get(
        "/v1/exports/excel", headers=auth, params={"schema": "auto", "from": today, "to": today}
    )
    assert r.status_code == 404


async def test_auto_never_in_schema_list(api, auth):
    r = await api.client.get("/v1/schemas", headers=auth)
    assert r.status_code == 200
    assert "auto" not in {s["slug"] for s in r.json()}


@pytest.mark.parametrize("slug", ["auto", "unknown"])
async def test_post_schema_slug_auto_422(api, auth, slug):
    body = builtin_schemas()["invoice_ru"].model_copy(update={"slug": slug}).model_dump(mode="json")
    r = await api.client.post("/v1/schemas", headers=auth, json=body)
    assert r.status_code == 422
    assert r.json()["detail"] == f"Служебное имя «{slug}» зарезервировано"


async def test_webhook_payload_for_failed_auto(api, auth):
    from docflow.serializers import document_payload

    doc = await get_doc(api, await failed_auto(api, auth))
    payload = document_payload(doc, "https://api.example.test")
    assert payload["schema"]["slug"] == "auto"
    assert payload["status"] == "failed"


# --- «Распознать заново» --------------------------------------------------------


async def test_retype_requeues_same_files(api, auth):
    doc_id = await failed_auto(api, auth, files=["originals/x/1.png", "originals/x/2.png"], pages=2)
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(doc_id))
        doc.claimed_at = doc.finished_at = doc.created_at
        await s.commit()
    r = await retype(api, auth, doc_id)
    assert r.status_code == 202, r.text
    assert r.json() == {"id": doc_id, "status": "queued"}
    doc = await get_doc(api, doc_id)
    latest = builtin_schemas()["invoice_ru"].version
    assert (doc.status, doc.schema_slug, doc.schema_version) == ("queued", "invoice_ru", latest)
    assert doc.files == ["originals/x/1.png", "originals/x/2.png"]
    assert doc.error is None and doc.claimed_at is None and doc.finished_at is None
    assert api.enqueuer.calls == [(PROCESS_DOCUMENT, (doc_id,), None, f"process:{doc_id}")]


async def test_retype_clears_bot_exports_only(api, auth):
    doc_id = await failed_auto(api, auth)
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(doc_id))
        dest = Destination(tenant_id=doc.tenant_id, kind="test_fake", name="d", config={}, mapping={}, active=True)
        bot = TenantBot(
            tenant_id=doc.tenant_id, token_enc="x", token_hash="h", username="test_bot", invite_code="c"
        )
        s.add_all([dest, bot])
        await s.flush()
        chat = BotChat(bot_id=bot.id, chat_id=5, title="Чат")
        s.add(chat)
        await s.flush()
        for purpose, chat_id in (("bot_ready", chat.id), ("bot_reviewed", chat.id), ("export", None)):
            s.add(
                Export(
                    tenant_id=doc.tenant_id,
                    document_id=doc.id,
                    destination_id=dest.id if chat_id is None else None,
                    purpose=purpose,
                    status="done",
                    bot_chat_id=chat_id,
                )
            )
        await s.commit()
    assert (await retype(api, auth, doc_id)).status_code == 202
    async with api.sessionmaker() as s:
        rows = (await s.execute(select(Export.purpose))).scalars().all()
    assert rows == ["export"]


async def test_retype_twice_second_409(api, auth):
    doc_id = await failed_auto(api, auth)
    assert (await retype(api, auth, doc_id)).status_code == 202
    second = await retype(api, auth, doc_id)
    assert second.status_code == 409
    assert second.json()["detail"] == CONFLICT
    assert len(api.enqueuer.calls) == 1


async def test_retype_rejected_when_type_known_or_not_failed(api, auth):
    known = await add_doc(api, auth, schema_slug="invoice_ru", status="failed")
    ok = await add_doc(api, auth, schema_slug="auto", status="ok")
    after_model = await failed_auto(api, auth, model="claude-x")
    for doc_id in (known, ok, after_model):
        r = await retype(api, auth, doc_id)
        assert r.status_code == 409, doc_id
        assert r.json()["detail"] == CONFLICT
    assert api.enqueuer.calls == []


@pytest.mark.parametrize("schema", ["auto", "no_such_type"])
async def test_retype_unknown_or_auto_422(api, auth, schema):
    doc_id = await failed_auto(api, auth)
    r = await retype(api, auth, doc_id, schema)
    assert r.status_code == 422
    assert (await get_doc(api, doc_id)).status == "failed"
    assert api.enqueuer.calls == []


async def test_retype_foreign_404(api, auth, make_tenant):
    doc_id = await failed_auto(api, auth)
    other = await make_tenant("Чужой")
    assert (await retype(api, other, doc_id)).status_code == 404
    assert (await retype(api, auth, str(uuid.uuid4()))).status_code == 404
    assert (await get_doc(api, doc_id)).status == "failed"


async def test_retype_over_limit_402(api, auth):
    await add_doc(api, auth, pages=50)
    doc_id = await failed_auto(api, auth, pages=3)
    r = await retype(api, auth, doc_id)
    assert r.status_code == 402
    assert r.json()["detail"] == LIMIT_TEXT
    assert (await get_doc(api, doc_id)).status == "failed"
    assert api.enqueuer.calls == []


async def test_retype_enqueue_failure_503_back_to_failed(api, auth, monkeypatch):
    doc_id = await failed_auto(api, auth)

    async def down(*_a, **_k):
        raise ConnectionError("redis недоступен")

    monkeypatch.setattr(api.enqueuer, "enqueue", down)
    r = await retype(api, auth, doc_id)
    assert r.status_code == 503
    doc = await get_doc(api, doc_id)
    assert (doc.status, doc.error) == ("failed", ENQUEUE_ERROR)
    assert doc.finished_at is not None
    assert (doc.schema_slug, doc.schema_version) == ("auto", 0)

    monkeypatch.undo()
    r = await retype(api, auth, doc_id)
    assert r.status_code == 202, r.text
    doc = await get_doc(api, doc_id)
    assert (doc.status, doc.schema_slug) == ("queued", "invoice_ru")


async def test_retype_enqueue_failure_after_worker_claimed_keeps_processing(api, auth, monkeypatch):
    doc_id = await failed_auto(api, auth)

    async def accepted_then_broken(job, document_id, **_k):
        await set_status(api, document_id, status="processing")
        raise ConnectionError("ответ Redis потерян")

    monkeypatch.setattr(api.enqueuer, "enqueue", accepted_then_broken)
    r = await retype(api, auth, doc_id)
    assert r.status_code == 202
    assert (await get_doc(api, doc_id)).status == "processing"
