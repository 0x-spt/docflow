import uuid
from collections import Counter
from datetime import date, timedelta, timezone

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from docflow.config import Settings
from docflow.db.models import (
    ApiKey,
    Destination,
    Dictionary,
    Document,
    Export,
    RoutingRules,
    SchemaRecord,
    Tenant,
    User,
)
from docflow.demo import seed as seed_module
from docflow.demo.data import DEMO_TENANT_NAME
from docflow.demo.seed import DemoError, DemoExists, SeedOptions, reset_demo, seed_demo
from docflow.pdf.build import NoOcr
from docflow.queue import DELIVER_EXPORT, ListEnqueuer
from docflow.secrets import decrypt_secrets
from docflow.storage.blobs import LocalBlobStore, original_key, scan_key

KEY = Fernet.generate_key().decode()
TODAY = date(2026, 10, 20)
MSK = timezone(timedelta(hours=3))
BITRIX = "https://demo-portal.bitrix24.ru/rest/1/abcdef123456/"


@pytest.fixture
def settings(digital_font):
    # digital_font кладёт шрифт с кириллицей в DOCFLOW_PDF_FONT_PATH: рендер фото его найдёт
    return Settings(
        secrets_key=KEY,
        webhook_allow_private=True,
        public_site_url="https://docflow.example.test",
    )


_PHOTOS: dict[tuple[str, int], bytes] = {}


@pytest.fixture(autouse=True)
def cached_render(monkeypatch):
    """Рендер детерминирован и занимает ~1 с на фото: между тестами модуля переиспользуем готовые."""
    real = seed_module.render_photo

    def render(scenario, schema_title, *, seed):
        key = (scenario.key, seed)
        if key not in _PHOTOS:
            _PHOTOS[key] = real(scenario, schema_title, seed=seed)
        return _PHOTOS[key]

    monkeypatch.setattr(seed_module, "render_photo", render)


@pytest.fixture
def blobs(tmp_path):
    return LocalBlobStore(tmp_path / "blobs")


async def _seed(sm, blobs, settings, **kw):
    enqueuer = ListEnqueuer()
    result = await seed_demo(sm, blobs, NoOcr(), enqueuer, settings, SeedOptions(today=TODAY, **kw))
    return result, enqueuer


async def _all(sm, model, *where):
    async with sm() as s:
        return list((await s.execute(select(model).where(*where))).scalars())


async def test_seed_creates_expected_mix(sm, blobs, settings):
    result, enqueuer = await _seed(sm, blobs, settings)
    assert result.counts == {"ok": 19, "needs_review": 3, "failed": 2}
    tid = result.tenant_id

    tenant = (await _all(sm, Tenant, Tenant.id == tid))[0]
    assert tenant.name == DEMO_TENANT_NAME and tenant.extractor == "ollama" and tenant.auto_ok
    users = await _all(sm, User, User.tenant_id == tid)
    assert sorted(u.role for u in users) == ["operator", "owner"]
    assert len(await _all(sm, ApiKey, ApiKey.tenant_id == tid)) == 1
    assert [r.slug for r in await _all(sm, SchemaRecord, SchemaRecord.tenant_id == tid)] == ["act_works"]
    assert [d.name for d in await _all(sm, Dictionary, Dictionary.tenant_id == tid)] == ["suppliers"]
    dests = {d.name: d for d in await _all(sm, Destination, Destination.tenant_id == tid)}
    assert set(dests) == {"Почта бухгалтерии", "Почта оператора"}
    assert dests["Почта бухгалтерии"].config["to_addrs"].startswith("buh@")
    assert dests["Почта оператора"].config["host"] == "mailpit"
    assert (await _all(sm, RoutingRules, RoutingRules.tenant_id == tid))[0].rules

    docs = await _all(sm, Document, Document.tenant_id == tid)
    assert Counter(d.status for d in docs) == {"ok": 19, "needs_review": 3, "failed": 2}
    assert {d.model for d in docs if d.status != "failed" or d.data} == {"demo"}
    exports = await _all(sm, Export, Export.tenant_id == tid)
    have = {(e.document_id, e.destination_id, e.purpose) for e in exports}
    acc, op = dests["Почта бухгалтерии"].id, dests["Почта оператора"].id
    for d in docs:
        if d.status == "ok":
            assert (d.id, acc, "notify_new") in have
        if d.status == "needs_review":
            assert (d.id, op, "notify_review") in have
    assert any(c[0] == DELIVER_EXPORT for c in enqueuer.calls)
    # одобренные с примечанием, отклонённый — дубль
    reviewed = [d for d in docs if d.reviewed_at is not None]
    assert len(reviewed) == 3 and all(d.review_note for d in reviewed)
    assert {d.reviewed_by for d in reviewed} == {next(u.id for u in users if u.role == "operator")}
    assert any(d.error and "Дубль счёта" in d.error for d in docs if d.status == "failed")


async def test_dates_backdated(sm, blobs, settings):
    result, _ = await _seed(sm, blobs, settings)
    docs = await _all(sm, Document, Document.tenant_id == result.tenant_id)
    for d in docs:
        assert TODAY - timedelta(days=21) <= d.created_at.astimezone(MSK).date() <= TODAY
        assert d.claimed_at >= d.created_at
        assert timedelta(0) <= d.finished_at - d.created_at < timedelta(hours=1)
        if d.reviewed_at is not None:
            assert d.reviewed_at > d.finished_at
    # даты разнесены, а не все в один день
    assert len({d.created_at.astimezone(MSK).date() for d in docs}) >= 10


async def test_seed_twice_refuses(sm, blobs, settings):
    await _seed(sm, blobs, settings)
    with pytest.raises(DemoExists):
        await _seed(sm, blobs, settings)
    assert len(await _all(sm, Document)) == 24
    assert len(await _all(sm, Tenant)) == 1


async def test_reset_recreates(sm, blobs, settings):
    first, _ = await _seed(sm, blobs, settings)
    old = await _all(sm, Document, Document.tenant_id == first.tenant_id)
    old_keys = [k for d in old for k in d.files] + [scan_key(str(d.id)) for d in old]
    assert all([await blobs.exists(k) for k in old_keys[:3]])

    second, _ = await _seed(sm, blobs, settings, reset=True)
    assert second.tenant_id != first.tenant_id
    assert not await _all(sm, Document, Document.tenant_id == first.tenant_id)
    assert not await _all(sm, Tenant, Tenant.id == first.tenant_id)
    for k in old_keys:
        assert not await blobs.exists(k)
    assert not list((blobs._root / "pages").glob(f"{old[0].id}/*"))
    assert len(await _all(sm, Document)) == 24


async def test_reset_keeps_other_tenants(sm, blobs, settings):
    await _seed(sm, blobs, settings)
    kept = []
    async with sm() as s:
        for name in ("Ромашка", DEMO_TENANT_NAME + " (копия)"):
            t = Tenant(name=name, extractor="claude")
            s.add(t)
            await s.flush()
            doc = Document(id=uuid.uuid4(), tenant_id=t.id, schema_slug="invoice_ru", schema_version=1, status="ok",
                           files=[original_key(str(t.id), 1, "x.jpg")], mode="gray")
            s.add(doc)
            kept.append((t.id, doc.id))
        await s.commit()
    await blobs.put(original_key(str(kept[0][0]), 1, "x.jpg"), b"x", "image/jpeg")

    assert await reset_demo(sm, blobs) is True
    assert await reset_demo(sm, blobs) is False
    for tenant_id, doc_id in kept:
        assert await _all(sm, Tenant, Tenant.id == tenant_id)
        assert await _all(sm, Document, Document.id == doc_id)
    assert await blobs.exists(original_key(str(kept[0][0]), 1, "x.jpg"))
    assert not await _all(sm, Tenant, Tenant.name == DEMO_TENANT_NAME)
    async with sm() as s:
        assert await s.scalar(select(func.count()).select_from(Document)) == 2


async def test_secret_flags_without_key_fail_before_create(sm, blobs, settings):
    no_key = settings.model_copy(update={"secrets_key": ""})
    with pytest.raises(DemoError):
        await _seed(sm, blobs, no_key, bitrix_webhook=BITRIX)
    assert not await _all(sm, Tenant)


async def test_half_given_pairs_fail(sm, blobs, settings):
    with pytest.raises(DemoError):
        await _seed(sm, blobs, settings, telegram_token="123:abc")
    with pytest.raises(DemoError):
        await _seed(sm, blobs, settings, google_sheet="sheet-id")
    assert not await _all(sm, Tenant)


async def test_optional_destinations(sm, blobs, settings):
    result, _ = await _seed(sm, blobs, settings, bitrix_webhook=BITRIX, telegram_token="123:abc",
                            telegram_chat="-100500")
    dests = {d.kind: d for d in await _all(sm, Destination, Destination.tenant_id == result.tenant_id)}
    assert sorted(dests) == ["bitrix24", "email", "telegram"]
    assert len(await _all(sm, Destination, Destination.tenant_id == result.tenant_id)) == 4
    rules = (await _all(sm, RoutingRules, RoutingRules.tenant_id == result.tenant_id))[0].rules
    ids = {i for r in rules for i in (*r["export_to"], *r["notify"])}
    assert {str(dests["bitrix24"].id), str(dests["telegram"].id)} <= ids
    assert decrypt_secrets(dests["bitrix24"].secrets_enc, KEY) == {"webhook_url": BITRIX}
    assert decrypt_secrets(dests["telegram"].secrets_enc, KEY) == {"bot_token": "123:abc"}
    assert dests["telegram"].config == {"chat_id": "-100500"}
    assert dests["bitrix24"].mapping == {}
    exports = await _all(sm, Export, Export.destination_id == dests["bitrix24"].id, Export.purpose == "export")
    assert exports


def test_timeline_clamps_future_and_keeps_order():
    from datetime import datetime

    from docflow.demo.data import scenarios

    today = date(2026, 10, 29)
    # самые свежие сценарии — 3 дня назад в 11 и 12 ч: «сейчас» раньше них, их надо прижать
    now = datetime(2026, 10, 26, 11, 30, tzinfo=MSK)
    line = seed_module._timeline(scenarios(today), today, now)
    assert any(sc.days_ago == 3 and sc.hour == 12 for sc, _ in line)
    times = [at for _, at in line]
    assert len(line) == 24 and times == sorted(times)
    assert all(at < now for at in times)
    assert all(at.date() >= today - timedelta(days=21) for at in times)
