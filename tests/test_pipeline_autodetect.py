import pytest
from test_pipeline import INVOICE, ChecksConnection, load, make_deps, make_doc, page_jpeg

from docflow.extraction.base import ExtractionError
from docflow.extraction.fake import FakeExtractor
from docflow.pdf.build import NoOcr
from docflow.pipeline import AUTODETECT_FAILED, Deps, SchemaResolver, process_document
from docflow.queue import DELIVER_WEBHOOK, ListEnqueuer
from docflow.storage.blobs import LocalBlobStore

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
    fake = FakeExtractor({"nakladnaya": INVOICE, "_classify": "nakladnaya"})
    deps = make_deps(sm, tmp_path, extractor=fake)
    body = {
        "slug": "nakladnaya",
        "title": "Накладная",
        "description": "Товарная накладная поставщика",
        "version": 1,
        "fields": [{"name": "number", "type": "string"}],
    }
    doc_id = await auto_doc(deps, schema_body=body)
    await process_document(deps, doc_id)

    types, _ = fake.classify_calls[0]
    own = next(t for t in types if t.slug == "nakladnaya")
    assert (own.title, own.description, own.builtin) == ("Накладная", "Товарная накладная поставщика", False)
    doc = await load(deps, doc_id)
    assert doc.schema_slug == "nakladnaya" and doc.status != "processing"


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

