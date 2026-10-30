from datetime import datetime

from sqlalchemy import select
from test_documents import add_doc, upload

from docflow.db.models import ApiKey, Tenant, hash_key
from docflow.plans import apply_plan

LIMIT_TEXT = "Лимит страниц на месяц исчерпан (50 из 50)"


async def set_plan(api, headers, code):
    async with api.sessionmaker() as s:
        tenant_id = (
            await s.execute(select(ApiKey.tenant_id).where(ApiKey.key_hash == hash_key(headers["Authorization"][7:])))
        ).scalar_one()
        tenant = await s.get(Tenant, tenant_id)
        apply_plan(tenant, code)
        await s.commit()


async def test_upload_over_limit_402(api, auth):
    await add_doc(api, auth, pages=50)
    r = await upload(api, auth)
    assert r.status_code == 402
    assert r.json()["detail"] == LIMIT_TEXT


async def test_usage_shape(api, auth):
    await add_doc(api, auth, pages=3)
    await add_doc(api, auth, pages=4, status="failed", model="x")
    await add_doc(api, auth, pages=9, status="failed")
    r = await api.client.get("/v1/usage", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert (body["plan"], body["plan_title"], body["pages_limit"], body["pages_used"]) == ("trial", "Пробный", 50, 7)
    start, end = datetime.fromisoformat(body["period_start"]), datetime.fromisoformat(body["period_end"])
    assert start < datetime.fromisoformat(body["period_end"]) and start.tzinfo is not None
    assert (end.month - start.month) % 12 in (0, 1)


async def test_usage_isolated_between_tenants(api, auth, make_tenant):
    await add_doc(api, auth, pages=5)
    other = await make_tenant("Чужой")
    await add_doc(api, other, pages=11)
    assert (await api.client.get("/v1/usage", headers=auth)).json()["pages_used"] == 5
    assert (await api.client.get("/v1/usage", headers=other)).json()["pages_used"] == 11


async def test_usage_unlimited_null_limit(api, auth):
    await set_plan(api, auth, "unlimited")
    body = (await api.client.get("/v1/usage", headers=auth)).json()
    assert body["pages_limit"] is None and body["plan"] == "unlimited"
    await add_doc(api, auth, pages=1000)
    assert (await upload(api, auth)).status_code == 202


async def test_usage_requires_auth(api):
    assert (await api.client.get("/v1/usage")).status_code == 401
