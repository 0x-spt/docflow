import uuid
from datetime import UTC, datetime

import pytest
from api_helpers import BASE_URL, pdf_bytes, png_bytes
from sqlalchemy import func, select

from docflow.db.models import ApiKey, Document
from docflow.queue import PROCESS_DOCUMENT
from docflow.storage.blobs import scan_key

AUTH_DETAIL = "неверный или отозванный ключ API"


def upload(api, headers, files=None, **form):
    files = files if files is not None else [("files", ("scan.png", png_bytes(), "image/png"))]
    data = {"schema": "invoice_ru", **form}
    return api.client.post("/v1/documents", headers=headers, files=files, data=data)


async def doc_count(api) -> int:
    async with api.sessionmaker() as s:
        return (await s.execute(select(func.count()).select_from(Document))).scalar_one()


async def set_status(api, doc_id: str, **values):
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(doc_id))
        for k, v in values.items():
            setattr(doc, k, v)
        await s.commit()


# --- авторизация ---------------------------------------------------------


@pytest.mark.parametrize(
    "headers",
    [{}, {"Authorization": "Bearer dfk_nope"}, {"Authorization": "Basic abc"}, {"Authorization": "Bearer"}],
)
async def test_401_without_valid_key(api, headers):
    r = await upload(api, headers)
    assert r.status_code == 401
    assert r.json() == {"detail": AUTH_DETAIL}
    assert r.headers["www-authenticate"] == "Bearer"
    assert api.enqueuer.calls == []


async def test_401_revoked_key(api, auth):
    async with api.sessionmaker() as s:
        key = (await s.execute(select(ApiKey))).scalar_one()
        key.revoked_at = datetime.now(UTC)
        await s.commit()
    r = await api.client.get(f"/v1/documents/{uuid.uuid4()}", headers=auth)
    assert r.status_code == 401
    assert r.json() == {"detail": AUTH_DETAIL}


# --- загрузка ------------------------------------------------------------


async def test_upload_202_enqueues(api, auth):
    r = await upload(api, auth)
    assert r.status_code == 202, r.text
    body = r.json()
    assert body["status"] == "queued"
    doc_id = body["id"]
    assert api.enqueuer.calls == [(PROCESS_DOCUMENT, (doc_id,), None, f"process:{doc_id}")]
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(doc_id))
    assert doc.status == "queued"
    assert doc.mode == "gray"
    assert (doc.schema_slug, doc.schema_version) == ("invoice_ru", 1)
    assert len(doc.files) == 1
    assert await api.blobs.get(doc.files[0]) == png_bytes()


async def test_upload_two_files_and_mode(api, auth):
    files = [
        ("files", ("a.png", png_bytes(), "image/png")),
        ("files", ("b.pdf", pdf_bytes(2), "application/pdf")),
    ]
    r = await upload(api, auth, files=files, mode="bw")
    assert r.status_code == 202, r.text
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(r.json()["id"]))
    assert doc.mode == "bw"
    assert len(doc.files) == 2
    assert await api.blobs.get(doc.files[1]) == pdf_bytes(2)


async def test_idempotency_returns_same_document(api, auth):
    h = {**auth, "Idempotency-Key": "abc-1"}
    r1 = await upload(api, h)
    r2 = await upload(api, h)
    assert r1.status_code == 202
    assert r2.status_code == 200
    assert r2.json() == r1.json()
    assert await doc_count(api) == 1
    assert len(api.enqueuer.calls) == 1


async def test_idempotency_key_is_per_tenant(api, auth, make_tenant):
    other = await make_tenant("Другой")
    r1 = await upload(api, {**auth, "Idempotency-Key": "k"})
    r2 = await upload(api, {**other, "Idempotency-Key": "k"})
    assert (r1.status_code, r2.status_code) == (202, 202)
    assert r1.json()["id"] != r2.json()["id"]


async def test_413_on_oversized_file(api, auth):
    big = b"\x89PNG\r\n\x1a\n" + b"\0" * (21 * 1024 * 1024)
    r = await upload(api, auth, files=[("files", ("big.png", big, "image/png"))])
    assert r.status_code == 413
    assert "detail" in r.json()
    assert await doc_count(api) == 0
    assert api.enqueuer.calls == []


async def test_415_on_garbage(api, auth):
    r = await upload(api, auth, files=[("files", ("x.pdf", b"hello", "application/pdf"))])
    assert r.status_code == 415
    assert "detail" in r.json()
    assert await doc_count(api) == 0


async def test_415_on_broken_file_with_valid_signature(api, auth):
    r = await upload(api, auth, files=[("files", ("x.pdf", b"%PDF-1.4 broken", "application/pdf"))])
    assert r.status_code == 415


async def test_422_unknown_schema(api, auth):
    r = await upload(api, auth, schema="no_such_schema")
    assert r.status_code == 422
    assert "no_such_schema" in r.json()["detail"]
    assert api.enqueuer.calls == []


async def test_422_unknown_schema_version(api, auth):
    r = await upload(api, auth, schema_version="7")
    assert r.status_code == 422


async def test_422_bad_mode(api, auth):
    r = await upload(api, auth, mode="sepia")
    assert r.status_code == 422


async def test_422_too_many_pages(api, auth):
    api.settings.max_pages = 3
    files = [
        ("files", ("a.pdf", pdf_bytes(2), "application/pdf")),
        ("files", ("b.pdf", pdf_bytes(2), "application/pdf")),
    ]
    r = await upload(api, auth, files=files)
    assert r.status_code == 422
    assert await doc_count(api) == 0


async def test_422_no_files(api, auth):
    r = await api.client.post("/v1/documents", headers=auth, data={"schema": "invoice_ru"})
    assert r.status_code == 422
    assert await doc_count(api) == 0


async def test_enqueue_failure_marks_failed_503(api, auth, monkeypatch):
    async def down(*_a, **_k):
        raise ConnectionError("redis недоступен")

    monkeypatch.setattr(api.enqueuer, "enqueue", down)
    r = await upload(api, auth)
    assert r.status_code == 503
    async with api.sessionmaker() as s:
        doc = (await s.execute(select(Document))).scalar_one()
    assert doc.status == "failed"
    assert doc.error


async def test_blob_failure_leaves_no_queued_document(api, auth, monkeypatch):
    async def boom(*_a, **_k):
        raise OSError("диск отвалился")

    monkeypatch.setattr(api.blobs, "put", boom)
    r = await upload(api, auth)
    assert r.status_code == 500
    assert "detail" in r.json()
    async with api.sessionmaker() as s:
        queued = (await s.execute(select(Document).where(Document.status == "queued"))).scalars().all()
    assert queued == []
    assert api.enqueuer.calls == []


async def test_upload_uses_tenant_schema_latest_version(api, auth):
    schema = {
        "slug": "my_doc",
        "title": "Мой документ",
        "description": "Тест",
        "fields": [{"name": "number", "type": "string", "description": "номер"}],
    }
    for _ in range(2):
        assert (await api.client.post("/v1/schemas", headers=auth, json=schema)).status_code == 201
    r = await upload(api, auth, schema="my_doc")
    assert r.status_code == 202
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(r.json()["id"]))
    assert (doc.schema_slug, doc.schema_version) == ("my_doc", 2)


# --- чтение --------------------------------------------------------------


async def test_get_document_payload(api, auth):
    doc_id = (await upload(api, auth)).json()["id"]
    r = await api.client.get(f"/v1/documents/{doc_id}", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {
        "id", "status", "schema", "data", "issues", "error", "pages", "model", "created_at", "finished_at", "pdf_url",
    }
    assert body["status"] == "queued"
    assert body["pdf_url"] is None
    await set_status(api, doc_id, status="ok", data={"number": "1"}, finished_at=datetime.now(UTC))
    body = (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).json()
    assert body["pdf_url"] == f"{BASE_URL}/v1/documents/{doc_id}/pdf"


async def test_404_other_tenant_document(api, auth, make_tenant):
    other = await make_tenant("Чужой")
    doc_id = (await upload(api, auth)).json()["id"]
    r = await api.client.get(f"/v1/documents/{doc_id}", headers=other)
    assert r.status_code == 404
    assert "detail" in r.json()
    r = await api.client.get(f"/v1/documents/{doc_id}/pdf", headers=other)
    assert r.status_code == 404


@pytest.mark.parametrize("bad", [str(uuid.uuid4()), "not-a-uuid"])
async def test_404_unknown_document(api, auth, bad):
    r = await api.client.get(f"/v1/documents/{bad}", headers=auth)
    assert r.status_code == 404


# --- PDF -----------------------------------------------------------------


@pytest.mark.parametrize("status", ["queued", "processing"])
async def test_pdf_409_before_ready(api, auth, status):
    doc_id = (await upload(api, auth)).json()["id"]
    await set_status(api, doc_id, status=status)
    r = await api.client.get(f"/v1/documents/{doc_id}/pdf", headers=auth)
    assert r.status_code == 409
    assert "detail" in r.json()


async def test_pdf_409_failed_without_pdf(api, auth):
    doc_id = (await upload(api, auth)).json()["id"]
    await set_status(api, doc_id, status="failed", error="плохо")
    r = await api.client.get(f"/v1/documents/{doc_id}/pdf", headers=auth)
    assert r.status_code == 409


async def test_pdf_streams_when_ready(api, auth):
    doc_id = (await upload(api, auth)).json()["id"]
    pdf = pdf_bytes(1)
    await api.blobs.put(scan_key(doc_id), pdf, "application/pdf")
    await set_status(api, doc_id, status="needs_review")
    r = await api.client.get(f"/v1/documents/{doc_id}/pdf", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == f'inline; filename="{doc_id}.pdf"'
    assert r.content == pdf


async def test_pdf_404_when_blob_missing_for_finished(api, auth):
    doc_id = (await upload(api, auth)).json()["id"]
    await set_status(api, doc_id, status="ok")
    r = await api.client.get(f"/v1/documents/{doc_id}/pdf", headers=auth)
    assert r.status_code == 404


# --- служебное -----------------------------------------------------------


async def test_healthz(api):
    r = await api.client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_openapi_meta(api):
    spec = (await api.client.get("/openapi.json")).json()
    assert spec["info"]["title"] == "DocFlow API"
    assert spec["info"]["version"] == "0.1.0"
    assert spec["paths"]["/v1/documents"]["post"]["description"]


async def test_idempotency_race_returns_winner(api, auth, monkeypatch):
    from docflow.api import documents

    h = {**auth, "Idempotency-Key": "race"}
    winner = (await upload(api, h)).json()
    # второй запрос «не увидел» победителя на шаге поиска и упёрся в UNIQUE при вставке
    real = documents._find_by_idempotency_key
    calls = []

    async def miss_once(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return None
        return await real(*args, **kwargs)

    monkeypatch.setattr(documents, "_find_by_idempotency_key", miss_once)
    r = await upload(api, h)
    assert r.status_code == 200, r.text
    assert r.json() == winner
    assert await doc_count(api) == 1
    assert len(api.enqueuer.calls) == 1
