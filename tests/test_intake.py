import sys
from pathlib import Path

import pytest
from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).parent / "api"))
from api_helpers import pdf_bytes, png_bytes  # noqa: E402

from docflow.config import Settings  # noqa: E402
from docflow.db.models import Document, Tenant  # noqa: E402
from docflow.intake import (  # noqa: E402
    IntakeDeps,
    IntakeEnqueueFailed,
    IntakeFile,
    IntakeInvalid,
    IntakeLimitExceeded,
    IntakeStorageFailed,
    IntakeTooLarge,
    IntakeUnsupported,
    accept_document,
)
from docflow.pipeline import SchemaResolver  # noqa: E402
from docflow.plans import apply_plan  # noqa: E402
from docflow.queue import PROCESS_DOCUMENT, ListEnqueuer  # noqa: E402
from docflow.storage.blobs import LocalBlobStore  # noqa: E402


@pytest.fixture
def deps(tmp_path):
    return IntakeDeps(
        settings=Settings(database_url="sqlite+aiosqlite:///:memory:", max_file_mb=20, max_pages=30),
        blobs=LocalBlobStore(tmp_path / "blobs"),
        enqueuer=ListEnqueuer(),
        resolver=SchemaResolver(),
    )


@pytest.fixture
async def tenant_id(sm):
    async with sm() as s:
        tenant = Tenant(name="t")
        s.add(tenant)
        await s.commit()
        return tenant.id


async def doc_count(sm) -> int:
    async with sm() as s:
        return (await s.execute(select(func.count()).select_from(Document))).scalar_one()


def png(name="a.png"):
    return IntakeFile.of(name, png_bytes())


async def test_accept_stores_source_ref_and_pages(sm, deps, tenant_id):
    async with sm() as s:
        result = await accept_document(
            s, deps, tenant_id=tenant_id, schema_slug="invoice_ru",
            files=[IntakeFile.of("a.pdf", pdf_bytes(2))], source="telegram", source_ref={"chat_id": 1},
        )
    assert result.created is True
    async with sm() as s:
        doc = await s.get(Document, result.document.id)
    assert (doc.pages, doc.source, doc.source_ref, doc.status) == (2, "telegram", {"chat_id": 1}, "queued")
    assert [c[:2] for c in deps.enqueuer.calls] == [(PROCESS_DOCUMENT, (str(doc.id),))]
    assert (await deps.blobs.get(doc.files[0])) is not None


async def test_replay_returns_existing_without_reading(sm, deps, tenant_id):
    async def boom(limit):
        raise AssertionError("файл не должен читаться")

    async with sm() as s:
        first = await accept_document(
            s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[png()], idempotency_key="k"
        )
    async with sm() as s:
        second = await accept_document(
            s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[IntakeFile("a.png", boom)],
            idempotency_key="k",
        )
    assert first.created is True
    assert second.created is False
    assert second.document.id == first.document.id


async def test_read_receives_file_limit(sm, deps, tenant_id):
    seen = []

    async def read(limit):
        seen.append(limit)
        return png_bytes()

    async with sm() as s:
        await accept_document(s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[IntakeFile("a.png", read)])
    assert seen == [deps.settings.max_file_mb * 1024 * 1024]


async def test_errors_map_to_classes(sm, deps, tenant_id):
    async def accept(files, slug="invoice_ru"):
        async with sm() as s:
            return await accept_document(s, deps, tenant_id=tenant_id, schema_slug=slug, files=files)

    with pytest.raises(IntakeInvalid):
        await accept([png()], slug="no_such_schema")
    with pytest.raises(IntakeInvalid):
        await accept([])
    with pytest.raises(IntakeUnsupported):
        await accept([IntakeFile.of("x.png", b"garbage" * 10)])
    with pytest.raises(IntakeInvalid):
        await accept([IntakeFile.of("a.pdf", pdf_bytes(31))])
    deps.settings.max_request_mb = 1
    part = png_bytes() + b"\0" * (600 * 1024)
    with pytest.raises(IntakeTooLarge):
        await accept([IntakeFile.of("a.png", part), IntakeFile.of("b.png", part)])
    assert await doc_count(sm) == 0


async def test_enqueue_failure_frees_key(sm, deps, tenant_id):
    async def fail(*args, **kwargs):
        raise RuntimeError("redis down")

    deps.enqueuer.enqueue = fail
    async with sm() as s:
        with pytest.raises(IntakeEnqueueFailed):
            await accept_document(
                s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[png()], idempotency_key="k"
            )
    async with sm() as s:
        doc = (await s.execute(select(Document))).scalar_one()
    assert doc.status == "failed"
    assert doc.idempotency_key is None


async def test_storage_failure_leaves_no_document(sm, deps, tenant_id):
    async def fail(*args, **kwargs):
        raise OSError("disk full")

    deps.blobs.put = fail
    async with sm() as s:
        with pytest.raises(IntakeStorageFailed):
            await accept_document(s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[png()])
    assert await doc_count(sm) == 0
    assert deps.enqueuer.calls == []


async def fill_pages(sm, tenant_id, pages):
    async with sm() as s:
        s.add(
            Document(
                tenant_id=tenant_id, schema_slug="invoice_ru", schema_version=1, status="ok", files=[], mode="gray",
                pages=pages,
            )
        )
        await s.commit()


async def test_limit_blocks_at_limit(sm, deps, tenant_id):
    await fill_pages(sm, tenant_id, 50)

    async def boom(limit):
        raise AssertionError("файл не должен читаться")

    async with sm() as s:
        with pytest.raises(IntakeLimitExceeded) as exc:
            await accept_document(
                s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[IntakeFile("a.png", boom)]
            )
    assert exc.value.message == "Лимит страниц на месяц исчерпан (50 из 50)"


async def test_document_started_within_limit_accepted_whole(sm, deps, tenant_id):
    await fill_pages(sm, tenant_id, 49)
    async with sm() as s:
        result = await accept_document(
            s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[IntakeFile.of("a.pdf", pdf_bytes(5))]
        )
    assert result.created is True
    async with sm() as s:
        with pytest.raises(IntakeLimitExceeded) as exc:
            await accept_document(s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[png()])
    assert exc.value.message == "Лимит страниц на месяц исчерпан (54 из 50)"


async def test_unlimited_plan_never_blocks(sm, deps, tenant_id):
    async with sm() as s:
        apply_plan(await s.get(Tenant, tenant_id), "unlimited")
        await s.commit()
    await fill_pages(sm, tenant_id, 100000)
    async with sm() as s:
        result = await accept_document(s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[png()])
    assert result.created is True


async def test_replay_over_limit_returns_existing(sm, deps, tenant_id):
    async with sm() as s:
        first = await accept_document(
            s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[png()], idempotency_key="k"
        )
    await fill_pages(sm, tenant_id, 100)
    async with sm() as s:
        second = await accept_document(
            s, deps, tenant_id=tenant_id, schema_slug="invoice_ru", files=[png()], idempotency_key="k"
        )
    assert second.created is False
    assert second.document.id == first.document.id
