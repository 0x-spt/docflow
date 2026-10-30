import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import select
from test_pipeline import INVOICE, ChecksConnection, load, make_deps, make_doc, page_jpeg, webhook_calls

from docflow.config import Settings
from docflow.db.models import BotChat, Document, Export, SchemaRecord, TenantBot
from docflow.exports import deliver_export
from docflow.extraction.base import ExtractionError
from docflow.extraction.fake import FakeExtractor
from docflow.pdf.build import NoOcr
from docflow.pipeline import AUTODETECT_FAILED, Deps, SchemaResolver, process_document, reap_stale_processing
from docflow.queue import DELIVER_EXPORT, DELIVER_WEBHOOK, ListEnqueuer
from docflow.secrets import encrypt_secrets
from docflow.storage.blobs import LocalBlobStore

sys.path.insert(0, str(Path(__file__).parent / "channels"))
from fake_telegram import FakeTelegram  # noqa: E402

BOT_TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOT_KEY = Fernet.generate_key().decode()
FILES = [(page_jpeg(bg=(250, 250, 250)), "p1.jpg"), (page_jpeg(bg=(10, 10, 10)), "p2.jpg")]


def auto_doc(deps, **kw):
    return make_doc(deps, kw.pop("files", FILES), slug="auto", version=0, **kw)


async def test_auto_detects_and_extracts(sm, tmp_path):
    fake = FakeExtractor({"invoice_ru": INVOICE, "_classify": "invoice_ru"})
    deps = make_deps(sm, tmp_path, extractor=fake)
    doc_id = await auto_doc(deps)
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "ok", doc.issues
    assert (doc.schema_slug, doc.schema_version) == ("invoice_ru", 1)
    assert doc.pages == 2
    assert len(fake.classify_calls) == 1
    types, image = fake.classify_calls[0]
    assert "invoice_ru" in [t.slug for t in types] and "auto" not in [t.slug for t in types]
    assert isinstance(image, bytes)
    # на извлечение ушли обе страницы, на определение — только первая
    assert fake.calls[0][0] == "invoice_ru" and len(fake.calls[0][1]) == 2
    assert fake.calls[0][1][0] == image


@pytest.mark.parametrize("answer", [None, "unknown", "nope"])
async def test_auto_unknown_fails(sm, tmp_path, answer):
    fake = FakeExtractor({"invoice_ru": INVOICE, "_classify": answer})
    deps = make_deps(sm, tmp_path, extractor=fake)
    doc_id = await auto_doc(deps)
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "failed" and doc.error == AUTODETECT_FAILED == "Не удалось определить тип документа"
    assert doc.schema_slug == "auto" and doc.model is None
    assert fake.calls == []
    assert len([c for c in deps.enqueuer.calls if c[0] == DELIVER_WEBHOOK]) == 1


class ClassifyRaises:
    model = "raises"

    def __init__(self, exc):
        self.exc = exc

    def classify(self, types, image):
        raise self.exc

    def extract(self, schema, images):
        raise AssertionError("не должно дойти до извлечения")


@pytest.mark.parametrize("exc", [ExtractionError("api_error"), RuntimeError("boom")])
async def test_auto_classify_error_fails(sm, tmp_path, exc):
    deps = make_deps(sm, tmp_path, extractor=ClassifyRaises(exc))
    doc_id = await auto_doc(deps)
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "failed" and doc.error == AUTODETECT_FAILED
    assert doc.finished_at is not None


class SpyRaises:
    model = "spy"

    def classify(self, types, image):
        raise AssertionError("облако для определения типа")

    def extract(self, schema, images):
        raise AssertionError("облако для извлечения")


async def test_local_tenant_never_uses_cloud_for_classify(sm, tmp_path):
    fake = FakeExtractor({"invoice_ru": INVOICE, "_classify": "invoice_ru"})
    deps = Deps(
        sessionmaker=sm,
        blobs=LocalBlobStore(tmp_path / "blobs"),
        ocr=NoOcr(),
        extractors={"claude": SpyRaises(), "ollama": fake},
        schemas=SchemaResolver(),
        enqueuer=ListEnqueuer(),
    )
    doc_id = await auto_doc(deps, extractor="ollama")
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "ok", doc.error
    assert len(fake.classify_calls) == 1 and len(fake.calls) == 1


async def test_auto_sees_tenant_types(sm, tmp_path):
    fake = FakeExtractor({"nakladnaya": {"number": "7"}, "_classify": "nakladnaya"})
    deps = make_deps(sm, tmp_path, extractor=fake)
    doc_id = await auto_doc(deps)  # документ ждёт определения: slug auto, версия 0
    body = {
        "slug": "nakladnaya",
        "title": "Накладная",
        "description": "Товарная накладная поставщика",
        "version": 1,
        "fields": [{"name": "number", "type": "string"}],
    }
    async with sm() as s:  # своя схема тенанта — версия 1, независимо от версии 0 документа
        tenant_id = (await s.get(Document, doc_id)).tenant_id
        s.add(SchemaRecord(tenant_id=tenant_id, slug="nakladnaya", version=1, body=body))
        await s.commit()
    await process_document(deps, doc_id)

    types, _ = fake.classify_calls[0]
    own = next(t for t in types if t.slug == "nakladnaya")
    assert (own.title, own.description, own.builtin) == ("Накладная", "Товарная накладная поставщика", False)
    doc = await load(deps, doc_id)
    assert doc.status in ("ok", "needs_review"), doc.error
    assert (doc.schema_slug, doc.schema_version) == ("nakladnaya", 1)
    assert doc.data == {"number": "7"}
    assert fake.calls[0][0] == "nakladnaya"


class ChecksConnectionClassify(ChecksConnection):
    def __init__(self, engine):
        super().__init__(engine)
        self.classify_checked_out = None

    def classify(self, types, image):
        self.classify_checked_out = self.engine.sync_engine.pool.checkedout()
        return "invoice_ru"


async def test_no_connection_held_during_classify(sm, tmp_path):
    ex = ChecksConnectionClassify(sm.kw["bind"])
    deps = make_deps(sm, tmp_path, extractor=ex)
    doc_id = await auto_doc(deps)
    await process_document(deps, doc_id)

    assert ex.classify_checked_out == 0 and ex.checked_out == 0
    assert (await load(deps, doc_id)).status == "ok"


async def link_bot(deps, doc_id):
    """Бот и чат тенанта документа; документ становится присланным из этого чата."""
    async with deps.sessionmaker() as s:
        document = await s.get(Document, doc_id)
        bot = TenantBot(
            tenant_id=document.tenant_id, token_enc=encrypt_secrets({"token": BOT_TOKEN}, BOT_KEY),
            token_hash="h", username="acme_bot", invite_code="code",
        )
        s.add(bot)
        await s.flush()
        s.add(BotChat(bot_id=bot.id, chat_id=500, title="Чат"))
        document.source = "telegram"
        document.source_ref = {"bot_id": str(bot.id), "chat_id": 500, "message_id": 7, "media_group_id": None}
        await s.commit()


async def bot_exports(deps, doc_id):
    async with deps.sessionmaker() as s:
        q = select(Export).where(Export.document_id == doc_id, Export.purpose == "bot_ready")
        return (await s.execute(q)).scalars().all()


async def test_pipeline_survives_bot_enqueue_failure(sm, tmp_path, monkeypatch):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    await link_bot(deps, doc_id)

    async def boom(*_a, **_k):
        raise RuntimeError("redis")

    monkeypatch.setattr("docflow.pipeline.enqueue_bot_messages", boom)
    await process_document(deps, doc_id)

    assert (await load(deps, doc_id)).status == "ok"
    assert len(webhook_calls(deps)) == 1


async def test_telegram_outage_does_not_block_document(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    await link_bot(deps, doc_id)
    await process_document(deps, doc_id)
    [export] = await bot_exports(deps, doc_id)

    tg = FakeTelegram()
    tg.fail_with["*"] = 500
    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", secrets_key=BOT_KEY)
    async with httpx.AsyncClient(transport=tg.transport) as http, sm() as s:
        await deliver_export(
            s, export.id, http=http, enqueuer=deps.enqueuer, blobs=deps.blobs, schemas=deps.schemas,
            settings=settings,
        )

    assert len(tg.calls) == 1
    assert (await load(deps, doc_id)).status == "ok"
    [export] = await bot_exports(deps, doc_id)
    assert export.status == "pending" and export.attempt == 1


async def test_reaper_notifies_bot(sm, tmp_path):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps, status="processing")
    await link_bot(deps, doc_id)
    now = datetime.now(UTC)
    async with sm() as s:
        (await s.get(Document, doc_id)).claimed_at = now - timedelta(minutes=11)
        await s.commit()

    async with sm() as s:
        assert await reap_stale_processing(s, deps.enqueuer, now, timedelta(minutes=10)) == 1

    assert (await load(deps, doc_id)).status == "failed"
    [export] = await bot_exports(deps, doc_id)
    assert export.status == "pending"
    assert [c[1][0] for c in deps.enqueuer.calls if c[0] == DELIVER_EXPORT] == [str(export.id)]
    assert len(webhook_calls(deps)) == 1

