import hashlib
import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from docflow.db.models import (
    ApiKey,
    Document,
    SchemaRecord,
    Tenant,
    WebhookDelivery,
    WebhookEndpoint,
    generate_api_key,
    hash_key,
)


async def _tenant(session, name="acme"):
    t = Tenant(name=name)
    session.add(t)
    await session.commit()
    return t


def _doc(tenant, **kw):
    return Document(tenant_id=tenant.id, schema_slug="invoice", schema_version=1, files=["a.png"], mode="fast", **kw)


async def test_tenant_defaults(session):
    t = await _tenant(session)
    assert isinstance(t.id, uuid.UUID)
    assert t.extractor == "claude"
    assert t.created_at is not None


def test_generate_api_key():
    plain, prefix, digest = generate_api_key()
    assert plain.startswith("dfk_") and len(plain) == 36
    assert prefix == plain[:8]
    assert digest == hash_key(plain) == hashlib.sha256(plain.encode()).hexdigest()


async def test_api_key_roundtrip_and_unique_hash(session):
    t = await _tenant(session)
    plain, prefix, digest = generate_api_key()
    session.add(ApiKey(tenant_id=t.id, prefix=prefix, key_hash=digest))
    await session.commit()
    got = (await session.execute(select(ApiKey).where(ApiKey.key_hash == hash_key(plain)))).scalar_one()
    assert got.revoked_at is None
    session.add(ApiKey(tenant_id=t.id, prefix=prefix, key_hash=digest))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_document_defaults(session):
    t = await _tenant(session)
    d = _doc(t)
    session.add(d)
    await session.commit()
    assert d.status == "queued"
    assert d.files == ["a.png"]
    assert d.data is None and d.finished_at is None
    assert d.pages == 0 and d.input_tokens == 0 and d.output_tokens == 0


async def test_idempotency_key_unique_per_tenant(session):
    t1, t2 = await _tenant(session, "a"), await _tenant(session, "b")
    session.add(_doc(t1, idempotency_key="k"))
    session.add(_doc(t2, idempotency_key="k"))
    await session.commit()
    session.add(_doc(t1, idempotency_key="k"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_null_idempotency_keys_do_not_collide(session):
    t = await _tenant(session)
    session.add_all([_doc(t), _doc(t)])
    await session.commit()


async def test_bad_status_rejected(session):
    t = await _tenant(session)
    session.add(_doc(t, status="weird"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_bad_extractor_rejected(session):
    session.add(Tenant(name="x", extractor="gpt"))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_schema_unique_tenant_slug_version(session):
    t = await _tenant(session)
    session.add(SchemaRecord(tenant_id=t.id, slug="s", version=1, body={"a": 1}))
    await session.commit()
    session.add(SchemaRecord(tenant_id=t.id, slug="s", version=1, body={}))
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_builtin_schema_has_null_tenant(session):
    session.add(SchemaRecord(slug="invoice", version=1, body={"fields": []}))
    await session.commit()


async def test_delete_tenant_cascades_keys_but_restricted_by_documents(session):
    t = await _tenant(session)
    session.add(ApiKey(tenant_id=t.id, prefix="dfk_aaaa", key_hash="h"))
    await session.commit()
    await session.delete(t)
    await session.commit()
    assert (await session.execute(select(ApiKey))).first() is None

    t2 = await _tenant(session, "b")
    session.add(_doc(t2))
    await session.commit()
    await session.delete(t2)
    with pytest.raises(IntegrityError):
        await session.commit()


async def test_webhook_delivery_unique_and_cascade(session):
    t = await _tenant(session)
    d = _doc(t)
    ep = WebhookEndpoint(tenant_id=t.id, url="https://x", secret="s", events=["document.done"])
    session.add_all([d, ep])
    await session.commit()
    assert ep.active is True
    session.add(WebhookDelivery(endpoint_id=ep.id, document_id=d.id, event="document.done"))
    await session.commit()
    dl = (await session.execute(select(WebhookDelivery))).scalar_one()
    assert dl.status == "pending" and dl.attempt == 0
    session.add(WebhookDelivery(endpoint_id=ep.id, document_id=d.id, event="document.done"))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    await session.delete(ep)
    await session.commit()
    assert (await session.execute(select(WebhookDelivery))).first() is None


async def test_builtin_schema_unique_slug_version(session):
    session.add(SchemaRecord(slug="invoice", version=1, body={}))
    await session.commit()
    session.add(SchemaRecord(slug="invoice", version=1, body={}))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
    session.add(SchemaRecord(slug="invoice", version=2, body={}))
    await session.commit()


async def test_datetimes_roundtrip_as_utc(session):
    from datetime import UTC, datetime

    t = await _tenant(session)
    k = ApiKey(tenant_id=t.id, prefix="dfk_aaaa", key_hash="h2", revoked_at=datetime(2026, 1, 1, tzinfo=UTC))
    session.add(k)
    await session.commit()
    key_id = k.id
    session.expire_all()
    got = (await session.execute(select(ApiKey).where(ApiKey.id == key_id))).scalar_one()
    assert got.revoked_at.tzinfo is not None and got.revoked_at.utcoffset().total_seconds() == 0
    assert got.revoked_at == datetime(2026, 1, 1, tzinfo=UTC)
    assert got.created_at.tzinfo is not None


async def test_naive_datetime_rejected(session):
    from datetime import datetime

    t = await _tenant(session)
    session.add(ApiKey(tenant_id=t.id, prefix="p", key_hash="h3", revoked_at=datetime(2026, 1, 1)))
    with pytest.raises(Exception, match="naive"):
        await session.commit()


def test_exports_tenant_created_index():
    """Журнал выгрузок тенанта «новые сверху» — по индексу (tenant_id, created_at)."""
    from docflow.db.models import Export

    idx = {i.name: [c.name for c in i.columns] for i in Export.__table__.indexes}
    assert idx.get("ix_exports_tenant_id_created_at") == ["tenant_id", "created_at"]
