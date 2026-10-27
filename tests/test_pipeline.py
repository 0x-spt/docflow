import asyncio
import io
import json
import time
import uuid

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import event

from docflow.db.models import Base, Document, SchemaRecord, Tenant, WebhookEndpoint
from docflow.db.session import make_engine, make_sessionmaker
from docflow.extraction.base import ExtractionError
from docflow.extraction.fake import FakeExtractor
from docflow.pdf.build import NoOcr
from docflow.pipeline import Deps, SchemaResolver, process_document
from docflow.queue import DELIVER_WEBHOOK, PROCESS_DOCUMENT, ListEnqueuer
from docflow.schemas.library import builtin_schemas
from docflow.storage.blobs import LocalBlobStore, original_key, scan_key

BIK = "044525225"
ACCOUNT = "40702810200000000001"  # валидный р/с для этого БИК (как в tests/validation/test_engine.py)

INVOICE = {
    "number": "15",
    "issued": "2025-01-15",
    "supplier": "ООО «Ромашка»",
    "supplier_inn": "7707083893",
    "supplier_kpp": "770701001",
    "buyer": None,
    "buyer_inn": None,
    "bik": BIK,
    "account": ACCOUNT,
    "corr_account": "30101810400000000225",
    "items": [{"name": "Услуга", "qty": 1, "unit": "усл.", "price": 250, "amount": 250}],
    "subtotal": 250,
    "total": 250,
    "vat": None,
    "_uncertain": [],
}


def page_jpeg(bg=(255, 255, 255), size=(600, 800)) -> bytes:
    img = Image.new("RGB", size, bg)
    draw = ImageDraw.Draw(img)
    for y in range(60, size[1] - 40, 50):
        draw.text((40, y), "СЧЁТ 12345 TEXT", fill=(20, 20, 20))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


@pytest.fixture
async def sm(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'db.sqlite').as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
    await engine.dispose()


class Raising:
    model = "raising"

    def __init__(self, exc):
        self.exc = exc

    def extract(self, schema, images):
        raise self.exc


class Slow:
    model = "slow"

    def extract(self, schema, images):
        time.sleep(0.5)
        raise AssertionError("не должно дойти до результата")


class BoomOcr:
    def add_text_layer(self, pdf: bytes) -> bytes:
        raise RuntimeError("tesseract упал")


def make_deps(sm, tmp_path, *, extractor=None, ocr=None, responses=None):
    return Deps(
        sessionmaker=sm,
        blobs=LocalBlobStore(tmp_path / "blobs"),
        ocr=ocr or NoOcr(),
        extractors={"claude": extractor or FakeExtractor(responses or {"invoice_ru": INVOICE})},
        schemas=SchemaResolver(),
        enqueuer=ListEnqueuer(),
    )


async def make_doc(deps, files=None, *, slug="invoice_ru", version=1, extractor="claude", status="queued"):
    files = files if files is not None else [(page_jpeg(), "scan.jpg")]
    async with deps.sessionmaker() as s:
        t = Tenant(name="t", extractor=extractor)
        s.add(t)
        await s.flush()
        doc_id = uuid.uuid4()
        keys = []
        for n, (data, name) in enumerate(files):
            key = original_key(str(doc_id), n, name)
            await deps.blobs.put(key, data, "application/octet-stream")
            keys.append(key)
        s.add(
            Document(
                id=doc_id, tenant_id=t.id, schema_slug=slug, schema_version=version, status=status,
                files=keys, mode="gray",
            )
        )
        s.add(
            WebhookEndpoint(
                tenant_id=t.id, url="https://client.example/hook", secret="s",
                events=["document.completed", "document.failed"],
            )
        )
        await s.commit()
    return doc_id


async def load(deps, doc_id) -> Document:
    async with deps.sessionmaker() as s:
        return await s.get(Document, doc_id)


def webhook_calls(deps):
    return [c for c in deps.enqueuer.calls if c[0] == DELIVER_WEBHOOK]


async def test_happy_path_ok(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "ok", doc.issues
    assert doc.data["total"] == 250.0
    assert "_uncertain" not in doc.data
    assert doc.issues == []
    assert doc.error is None
    assert doc.model == "fake"
    assert doc.pages == 1
    assert doc.finished_at is not None
    assert await deps.blobs.exists(scan_key(str(doc_id)))
    assert (await deps.blobs.get(scan_key(str(doc_id)))).startswith(b"%PDF-")
    assert len(webhook_calls(deps)) == 1


async def test_sum_mismatch_needs_review(sm, tmp_path):
    deps = make_deps(sm, tmp_path, responses={"invoice_ru": {**INVOICE, "subtotal": 300}})
    doc_id = await make_doc(deps)
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "needs_review"
    assert "sum_mismatch" in [i["code"] for i in doc.issues]
    assert all(set(i) == {"field", "code", "message"} for i in doc.issues)


async def test_multipage_order(sm, tmp_path):
    fake = FakeExtractor({"invoice_ru": INVOICE})
    deps = make_deps(sm, tmp_path, extractor=fake)
    colors = [(255, 0, 0), (0, 255, 0), (0, 0, 255)]
    files = [(page_jpeg(bg=c), f"p{i}.jpg") for i, c in enumerate(colors)]
    doc_id = await make_doc(deps, files)
    await process_document(deps, doc_id)

    assert len(fake.calls) == 1
    slug, images = fake.calls[0]
    assert slug == "invoice_ru"
    assert len(images) == 3
    dominant = []
    for jpeg in images:
        r, g, b = Image.open(io.BytesIO(jpeg)).convert("RGB").resize((1, 1), Image.Resampling.BOX).getpixel((0, 0))
        dominant.append(max(range(3), key=lambda k: (r, g, b)[k]))
    assert dominant == [0, 1, 2]
    assert (await load(deps, doc_id)).pages == 3


async def test_refusal_failed(sm, tmp_path):
    deps = make_deps(sm, tmp_path, extractor=Raising(ExtractionError("refusal")))
    doc_id = await make_doc(deps)
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "failed"
    assert "refusal" in doc.error
    assert doc.finished_at is not None
    assert len(webhook_calls(deps)) == 1


async def test_garbage_failed(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps, [(b"hello", "x.pdf")])
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "failed"
    assert "x.pdf" in doc.error
    assert len(webhook_calls(deps)) == 1


async def test_rerun_is_noop(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    await process_document(deps, doc_id)
    first = await load(deps, doc_id)
    calls = list(deps.enqueuer.calls)

    await process_document(deps, doc_id)
    second = await load(deps, doc_id)
    assert second.status == first.status == "ok"
    assert second.finished_at == first.finished_at
    assert second.data == first.data
    assert deps.enqueuer.calls == calls


async def test_not_queued_is_noop(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps, status="processing")
    await process_document(deps, doc_id)
    doc = await load(deps, doc_id)
    assert doc.status == "processing" and doc.finished_at is None
    assert deps.enqueuer.calls == []


async def test_missing_document_is_noop(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    await process_document(deps, uuid.uuid4())
    assert deps.enqueuer.calls == []


async def test_ocr_failure_keeps_image_pdf(sm, tmp_path):
    deps = make_deps(sm, tmp_path, ocr=BoomOcr())
    doc_id = await make_doc(deps)
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status in ("ok", "needs_review")
    assert (await deps.blobs.get(scan_key(str(doc_id)))).startswith(b"%PDF-")


async def test_unexpected_exception_failed(sm, tmp_path, caplog):
    deps = make_deps(sm, tmp_path, extractor=Raising(RuntimeError("boom")))
    doc_id = await make_doc(deps)
    await process_document(deps, doc_id)  # не бросает

    doc = await load(deps, doc_id)
    assert doc.status == "failed"
    assert doc.error == "внутренняя ошибка обработки"
    assert doc.finished_at is not None
    assert len(webhook_calls(deps)) == 1
    assert any(r.exc_info for r in caplog.records)


async def test_unknown_extractor_failed(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps, extractor="ollama")
    await process_document(deps, doc_id)
    doc = await load(deps, doc_id)
    assert doc.status == "failed" and "ollama" in doc.error


async def test_unknown_schema_failed(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps, version=99)
    await process_document(deps, doc_id)
    doc = await load(deps, doc_id)
    assert doc.status == "failed" and "invoice_ru" in doc.error


async def test_timeout_marks_failed(sm, tmp_path):
    deps = make_deps(sm, tmp_path, extractor=Slow())
    doc_id = await make_doc(deps)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(process_document(deps, doc_id), timeout=0.3)
    doc = await load(deps, doc_id)
    assert doc.status == "failed"
    assert doc.finished_at is not None


# --- SchemaResolver -------------------------------------------------------


def _body(slug, version, title):
    s = builtin_schemas()["invoice_ru"].model_dump()
    return {**s, "slug": slug, "version": version, "title": title}


async def test_resolver_order(sm):
    r = SchemaResolver()
    async with sm() as s:
        t = Tenant(name="t")
        other = Tenant(name="o")
        s.add_all([t, other])
        await s.flush()

        # без строк в БД — встроенная библиотека
        assert (await r.resolve(s, t.id, "invoice_ru", None)).title == builtin_schemas()["invoice_ru"].title
        assert (await r.resolve(s, t.id, "invoice_ru", 1)).version == 1
        with pytest.raises(LookupError):
            await r.resolve(s, t.id, "invoice_ru", 2)
        with pytest.raises(LookupError):
            await r.resolve(s, t.id, "nope", None)

        s.add_all([
            SchemaRecord(tenant_id=None, slug="invoice_ru", version=1, body=_body("invoice_ru", 1, "b1")),
            SchemaRecord(tenant_id=None, slug="invoice_ru", version=2, body=_body("invoice_ru", 2, "b2")),
            SchemaRecord(tenant_id=other.id, slug="invoice_ru", version=5, body=_body("invoice_ru", 5, "o5")),
        ])
        await s.flush()
        assert (await r.resolve(s, t.id, "invoice_ru", None)).title == "b2"
        assert (await r.resolve(s, t.id, "invoice_ru", 1)).title == "b1"

        s.add_all([
            SchemaRecord(tenant_id=t.id, slug="invoice_ru", version=3, body=_body("invoice_ru", 3, "t3")),
            SchemaRecord(tenant_id=t.id, slug="invoice_ru", version=1, body=_body("invoice_ru", 1, "t1")),
        ])
        await s.flush()
        assert (await r.resolve(s, t.id, "invoice_ru", None)).title == "t3"
        assert (await r.resolve(s, t.id, "invoice_ru", 1)).title == "t1"
        assert (await r.resolve(s, t.id, "invoice_ru", 2)).title == "b2"


# --- worker ---------------------------------------------------------------


def test_worker_settings_registered():
    from arq.cron import CronJob

    from docflow import worker

    ws = worker.WorkerSettings
    names = {f.name for f in ws.functions}
    assert names == {PROCESS_DOCUMENT, DELIVER_WEBHOOK}
    assert ws.max_jobs == 4 and ws.job_timeout == 300
    assert ws.on_startup is worker.startup and ws.on_shutdown is worker.shutdown
    assert len(ws.cron_jobs) == 1
    cron = ws.cron_jobs[0]
    assert isinstance(cron, CronJob) and cron.coroutine is worker.requeue_stale_task
    assert cron.minute == set(range(0, 60, 5))
    assert ws.redis_settings.host


async def test_worker_startup_fake_mode(tmp_path, monkeypatch):
    from docflow import worker
    from docflow.config import get_settings

    responses = tmp_path / "fake.json"
    responses.write_text(json.dumps({"invoice_ru": INVOICE}), encoding="utf-8")
    monkeypatch.setenv("DOCFLOW_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'w.sqlite').as_posix()}")
    monkeypatch.setenv("DOCFLOW_BLOB_ROOT", str(tmp_path / "blobs"))
    monkeypatch.setenv("DOCFLOW_FAKE_EXTRACTOR", "1")
    monkeypatch.setenv("DOCFLOW_FAKE_RESPONSES", str(responses))
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        ctx = {"redis": object()}
        await worker.startup(ctx)
        deps = ctx["deps"]
        assert set(deps.extractors) == {"claude", "ollama"}
        assert all(isinstance(e, FakeExtractor) for e in deps.extractors.values())
        assert deps.extractors["claude"].responses == {"invoice_ru": INVOICE}
        await worker.shutdown(ctx)
        assert ctx["http"].is_closed
    finally:
        get_settings.cache_clear()


async def test_worker_startup_real_mode_without_key(tmp_path, monkeypatch):
    """Без ANTHROPIC_API_KEY воркер всё равно стартует (ключ нужен только Claude и только при запросе)."""
    from docflow import worker
    from docflow.config import get_settings
    from docflow.extraction.claude import ClaudeExtractor
    from docflow.extraction.ollama import OllamaExtractor

    monkeypatch.setenv("DOCFLOW_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'w.sqlite').as_posix()}")
    monkeypatch.setenv("DOCFLOW_FAKE_EXTRACTOR", "0")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    get_settings.cache_clear()
    try:
        ctx = {"redis": object()}
        await worker.startup(ctx)
        extractors = ctx["deps"].extractors
        assert isinstance(extractors["claude"], ClaudeExtractor)
        assert extractors["claude"].model == get_settings().anthropic_model
        assert isinstance(extractors["ollama"], OllamaExtractor)
        await worker.shutdown(ctx)
    finally:
        get_settings.cache_clear()
