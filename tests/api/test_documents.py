import uuid
from datetime import UTC, datetime

import pytest
from api_helpers import BASE_URL, pdf_bytes, png_bytes
from sqlalchemy import func, select

from docflow.db.models import ApiKey, Document
from docflow.queue import PROCESS_DOCUMENT
from docflow.storage.blobs import page_key, scan_key

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
        ("files", ("b.pdf", pdf := pdf_bytes(2), "application/pdf")),
    ]
    r = await upload(api, auth, files=files, mode="bw")
    assert r.status_code == 202, r.text
    async with api.sessionmaker() as s:
        doc = await s.get(Document, uuid.UUID(r.json()["id"]))
    assert doc.mode == "bw"
    assert len(doc.files) == 2
    assert await api.blobs.get(doc.files[1]) == pdf  # img2pdf пишет время в PDF: второй вызов может отличаться


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
    assert doc.finished_at is not None


async def test_enqueue_failure_after_worker_claimed_is_202(api, auth, monkeypatch):
    """Redis принял задачу и оборвал ответ, воркер уже забрал документ: его не перетираем в failed."""

    async def accepted_then_broken(job, document_id, **_k):
        await set_status(api, document_id, status="processing")
        raise ConnectionError("ответ Redis потерян")

    monkeypatch.setattr(api.enqueuer, "enqueue", accepted_then_broken)
    h = {**auth, "Idempotency-Key": "keep-me"}
    r = await upload(api, h)
    assert r.status_code == 202, r.text
    async with api.sessionmaker() as s:
        doc = (await s.execute(select(Document))).scalar_one()
    assert doc.status == "processing" and doc.error is None and doc.finished_at is None
    assert doc.idempotency_key == "keep-me"


async def test_enqueue_failure_frees_idempotency_key(api, auth, monkeypatch):
    h = {**auth, "Idempotency-Key": "retry-me"}
    real = api.enqueuer.enqueue

    async def down(*_a, **_k):
        raise ConnectionError("redis недоступен")

    monkeypatch.setattr(api.enqueuer, "enqueue", down)
    failed = await upload(api, h)
    assert failed.status_code == 503
    monkeypatch.setattr(api.enqueuer, "enqueue", real)
    r = await upload(api, h)
    assert r.status_code == 202, r.text
    assert r.json()["status"] == "queued"
    async with api.sessionmaker() as s:
        docs = (await s.execute(select(Document))).scalars().all()
    assert len(docs) == 2
    assert {d.idempotency_key for d in docs} == {None, "retry-me"}


async def test_idempotent_replay_ignores_files(api, auth):
    h = {**auth, "Idempotency-Key": "same"}
    first = await upload(api, h)
    garbage = [("files", ("x.bin", b"hello", "application/octet-stream"))]
    r = await upload(api, h, files=garbage)
    assert r.status_code == 200
    assert r.json() == first.json()


async def test_idempotency_key_too_long_422(api, auth):
    r = await upload(api, {**auth, "Idempotency-Key": "k" * 256})
    assert r.status_code == 422
    assert await doc_count(api) == 0
    r = await upload(api, {**auth, "Idempotency-Key": "k" * 255})
    assert r.status_code == 202


async def test_too_many_files_422_before_reading(api, auth, monkeypatch):
    from docflow.api import documents

    api.settings.max_pages = 2
    reads = []
    real = documents._read_capped

    async def spy(*args, **kwargs):
        reads.append(1)
        return await real(*args, **kwargs)

    monkeypatch.setattr(documents, "_read_capped", spy)
    files = [("files", (f"{i}.png", png_bytes(), "image/png")) for i in range(3)]
    r = await upload(api, auth, files=files)
    assert r.status_code == 422
    assert reads == []
    assert await doc_count(api) == 0


async def test_total_bytes_cap_in_handler(api, auth, monkeypatch):
    from docflow.api import documents

    # middleware пропускает (лимит проверяется по всему телу), а обработчик считает сумму файлов
    monkeypatch.setattr(documents, "_request_limit", lambda settings: 1024 * 1024)
    part = png_bytes() + b"\0" * (600 * 1024)  # валидный PNG с хвостом: страница считается
    files = [("files", ("a.png", part, "image/png")), ("files", ("b.png", part, "image/png"))]
    r = await upload(api, auth, files=files)
    assert r.status_code == 413
    assert await doc_count(api) == 0


async def test_blob_content_type_from_signature(api, auth, monkeypatch):
    seen = []
    real = api.blobs.put

    async def spy(key, data, content_type):
        seen.append(content_type)
        await real(key, data, content_type)

    monkeypatch.setattr(api.blobs, "put", spy)
    files = [
        ("files", ("a.png", png_bytes(), "text/html")),
        ("files", ("b.pdf", pdf_bytes(1), "image/png")),
    ]
    r = await upload(api, auth, files=files)
    assert r.status_code == 202
    assert seen == ["image/png", "application/pdf"]


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
        "id", "status", "schema", "data", "issues", "error", "pages", "model", "created_at", "finished_at",
        "review", "pdf_url", "digital_pdf_url",
    }
    assert body["status"] == "queued"
    assert body["pdf_url"] is None and body["digital_pdf_url"] is None
    await set_status(api, doc_id, status="ok", data={"number": "1"}, finished_at=datetime.now(UTC))
    body = (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).json()
    assert body["pdf_url"] == f"{BASE_URL}/v1/documents/{doc_id}/pdf"
    assert body["digital_pdf_url"] == f"{BASE_URL}/v1/documents/{doc_id}/digital.pdf"


async def test_payload_digital_url(api, auth):
    doc_id = (await upload(api, auth)).json()["id"]
    await set_status(api, doc_id, status="failed", error="плохо")
    assert (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).json()["digital_pdf_url"] is None
    await set_status(api, doc_id, status="needs_review", data={})
    body = (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).json()
    assert body["digital_pdf_url"].endswith(f"/v1/documents/{doc_id}/digital.pdf")


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


async def test_upload_pinned_builtin_version_with_tenant_override(api, auth):
    """Тенант перекрыл invoice_ru (его версия — v2), но schema_version=1 берёт встроенную схему."""
    from docflow.schemas.library import builtin_schemas

    builtin = builtin_schemas()["invoice_ru"]
    override = {
        "slug": "invoice_ru",
        "title": "Наш счёт",
        "description": "d",
        "fields": [{"name": "number", "type": "string", "description": "номер"}],
    }
    assert (await api.client.post("/v1/schemas", headers=auth, json=override)).json()["version"] == builtin.version + 1

    pinned = await upload(api, auth, schema_version=str(builtin.version))
    latest = await upload(api, auth)
    assert (pinned.status_code, latest.status_code) == (202, 202)
    async with api.sessionmaker() as s:
        p = await s.get(Document, uuid.UUID(pinned.json()["id"]))
        l_ = await s.get(Document, uuid.UUID(latest.json()["id"]))
    assert (p.schema_slug, p.schema_version) == ("invoice_ru", builtin.version)
    assert (l_.schema_slug, l_.schema_version) == ("invoice_ru", builtin.version + 1)
    # и воркер по закреплённой версии возьмёт тело встроенной схемы
    from docflow.pipeline import SchemaResolver

    async with api.sessionmaker() as s:
        resolved = await SchemaResolver().resolve(s, p.tenant_id, "invoice_ru", builtin.version)
    assert resolved.title == builtin.title and len(resolved.fields) == len(builtin.fields)


# --- список и превью страниц ----------------------------------------------


async def add_doc(api, headers, *, created_at=None, **values):
    """Документ тенанта, чей ключ в headers, прямо в БД."""
    from docflow.db.models import ApiKey as Key
    from docflow.db.models import hash_key

    async with api.sessionmaker() as s:
        tenant_id = (
            await s.execute(select(Key.tenant_id).where(Key.key_hash == hash_key(headers["Authorization"][7:])))
        ).scalar_one()
        doc = Document(
            tenant_id=tenant_id,
            schema_slug=values.pop("schema_slug", "invoice_ru"),
            schema_version=1,
            status=values.pop("status", "ok"),
            files=[],
            mode="gray",
            **values,
        )
        if created_at is not None:
            doc.created_at = created_at
        s.add(doc)
        await s.commit()
        return str(doc.id)


async def test_list_newest_first_and_pagination(api, auth):
    base = datetime(2026, 10, 1, 12, tzinfo=UTC)
    ids = [await add_doc(api, auth, created_at=base.replace(minute=m)) for m in (1, 2, 3)]

    r = await api.client.get("/v1/documents", headers=auth, params={"limit": 2})
    assert r.status_code == 200
    body = r.json()
    assert [i["id"] for i in body["items"]] == [ids[2], ids[1]]
    assert body["next_before"] is not None

    r = await api.client.get("/v1/documents", headers=auth, params={"limit": 2, "before": body["next_before"]})
    body = r.json()
    assert [i["id"] for i in body["items"]] == [ids[0]]
    assert body["next_before"] is None


async def test_list_before_naive_is_utc_and_invalid_422(api, auth):
    await add_doc(api, auth, created_at=datetime(2026, 10, 1, 12, tzinfo=UTC))
    r = await api.client.get("/v1/documents", headers=auth, params={"before": "2026-10-01T12:00:01"})
    assert len(r.json()["items"]) == 1
    r = await api.client.get("/v1/documents", headers=auth, params={"before": "2026-10-01T11:59:59Z"})
    assert r.json()["items"] == []
    assert (await api.client.get("/v1/documents", headers=auth, params={"before": "вчера"})).status_code == 422


async def test_list_filter_status(api, auth):
    ok = await add_doc(api, auth, status="ok")
    await add_doc(api, auth, status="failed")
    r = await api.client.get("/v1/documents", headers=auth, params={"status": "ok"})
    assert [i["id"] for i in r.json()["items"]] == [ok]


async def test_list_isolated_between_tenants(api, auth, make_tenant):
    other = await make_tenant("Чужой")
    await add_doc(api, other)
    mine = await add_doc(api, auth)
    r = await api.client.get("/v1/documents", headers=auth)
    assert [i["id"] for i in r.json()["items"]] == [mine]


async def test_list_item_shape(api, auth):
    data = {"number": "15", "supplier": "ООО", "total": 250, "empty": "", "none": None, "items": [1], "a": "x"}
    issues = [
        {"field": "total", "code": "corrected", "message": "исправлено"},
        {"field": "total", "code": "sum_mismatch", "message": "итог не сходится"},
    ]
    doc_id = await add_doc(api, auth, status="needs_review", data=data, issues=issues)
    bare = await add_doc(api, auth, status="queued")

    items = {i["id"]: i for i in (await api.client.get("/v1/documents", headers=auth)).json()["items"]}
    item = items[doc_id]
    assert item["title"] == "15"
    assert item["summary"] == [
        {"field": "supplier", "value": "ООО"},
        {"field": "total", "value": 250},
        {"field": "a", "value": "x"},
    ]
    assert item["issue"] == "итог не сходится"
    assert item["schema"] == {"slug": "invoice_ru", "version": 1}
    assert item["status"] == "needs_review"
    assert item["finished_at"] is None
    assert items[bare]["title"] == bare[:8]
    assert items[bare]["summary"] == [] and items[bare]["issue"] is None


async def test_list_bad_status_422(api, auth):
    assert (await api.client.get("/v1/documents", headers=auth, params={"status": "x"})).status_code == 422
    assert (await api.client.get("/v1/documents", headers=auth, params={"limit": 0})).status_code == 422


async def test_page_preview_served(api, auth):
    doc_id = await add_doc(api, auth)
    await api.blobs.put(page_key(doc_id, 1), b"\xff\xd8jpeg", "image/jpeg")
    r = await api.client.get(f"/v1/documents/{doc_id}/pages/1", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.content == b"\xff\xd8jpeg"


async def test_page_preview_missing_404(api, auth):
    doc_id = await add_doc(api, auth)
    assert (await api.client.get(f"/v1/documents/{doc_id}/pages/1", headers=auth)).status_code == 404
    assert (await api.client.get(f"/v1/documents/{doc_id}/pages/0", headers=auth)).status_code == 404


async def test_page_preview_foreign_404(api, auth, make_tenant):
    other = await make_tenant("Чужой")
    doc_id = await add_doc(api, other)
    await api.blobs.put(page_key(doc_id, 1), b"\xff\xd8jpeg", "image/jpeg")
    assert (await api.client.get(f"/v1/documents/{doc_id}/pages/1", headers=auth)).status_code == 404


# --- цифровая версия -----------------------------------------------------


async def test_digital_pdf_endpoint_ok(digital_font, api, auth):
    import pypdfium2 as pdfium

    doc_id = (await upload(api, auth)).json()["id"]
    data = {"number": "СЧ-15/А", "issued": "2026-03-05", "total": 12500.5, "items": []}
    await set_status(api, doc_id, status="ok", data=data, finished_at=datetime.now(UTC))
    r = await api.client.get(f"/v1/documents/{doc_id}/digital.pdf", headers=auth)
    assert r.status_code == 200
    assert r.headers["content-type"] == "application/pdf"
    assert r.headers["content-disposition"] == 'inline; filename="invoice_ru-15.pdf"'
    assert "12 500,50" in pdfium.PdfDocument(r.content)[0].get_textpage().get_text_range()


async def test_digital_pdf_filename_falls_back_to_id(digital_font, api, auth):
    doc_id = (await upload(api, auth)).json()["id"]
    await set_status(api, doc_id, status="needs_review", data={"number": "№ ?"})
    r = await api.client.get(f"/v1/documents/{doc_id}/digital.pdf", headers=auth)
    assert r.headers["content-disposition"] == f'inline; filename="invoice_ru-{doc_id[:8]}.pdf"'


@pytest.mark.parametrize("status", ["queued", "processing", "failed"])
async def test_digital_pdf_conflict_for_failed(api, auth, status):
    doc_id = (await upload(api, auth)).json()["id"]
    await set_status(api, doc_id, status=status)
    r = await api.client.get(f"/v1/documents/{doc_id}/digital.pdf", headers=auth)
    assert r.status_code == 409
    assert "ok и needs_review" in r.json()["detail"]


async def test_digital_pdf_foreign_404(api, auth, make_tenant):
    other = await make_tenant("Чужой")
    doc_id = (await upload(api, auth)).json()["id"]
    await set_status(api, doc_id, status="ok", data={})
    assert (await api.client.get(f"/v1/documents/{doc_id}/digital.pdf", headers=other)).status_code == 404
