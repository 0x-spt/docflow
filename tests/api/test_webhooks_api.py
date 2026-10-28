import uuid

import pytest
from api_helpers import session_for
from sqlalchemy import select

from docflow.db.models import WebhookEndpoint

PUBLIC_URL = "https://93.184.216.34/hook"  # IP-литерал: проверка SSRF обходится без DNS


async def create(api, headers, url=PUBLIC_URL, events=("document.completed",)):
    return await api.client.post("/v1/webhooks", headers=headers, json={"url": url, "events": list(events)})


async def test_create_returns_secret_once(api, auth):
    r = await create(api, auth, events=["document.completed", "document.failed"])
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["secret"] and len(body["secret"]) >= 40
    uuid.UUID(body["id"])
    async with api.sessionmaker() as s:
        ep = await s.get(WebhookEndpoint, uuid.UUID(body["id"]))
    assert ep.secret == body["secret"]
    listed = (await api.client.get("/v1/webhooks", headers=auth)).json()
    assert len(listed) == 1
    assert listed[0]["id"] == body["id"]
    assert listed[0]["url"] == PUBLIC_URL
    assert sorted(listed[0]["events"]) == ["document.completed", "document.failed"]
    assert "secret" not in listed[0]
    assert body["secret"] not in str(listed)


@pytest.mark.parametrize("events", [[], ["document.created"], ["document.completed", "nope"]])
async def test_bad_events_422(api, auth, events):
    r = await create(api, auth, events=events)
    assert r.status_code == 422
    assert "detail" in r.json()


@pytest.mark.parametrize("url", ["http://127.0.0.1/hook", "http://10.0.0.5/x", "ftp://93.184.216.34/x", "nope"])
async def test_private_or_bad_url_422(api, auth, url):
    r = await create(api, auth, url=url)
    assert r.status_code == 422
    assert "detail" in r.json()


async def test_url_too_long_422(api, auth):
    r = await create(api, auth, url=PUBLIC_URL + "?q=" + "a" * 2048)
    assert r.status_code == 422


async def test_private_url_allowed_when_setting_on(api, auth):
    api.settings.webhook_allow_private = True
    r = await create(api, auth, url="http://127.0.0.1:9000/hook")
    assert r.status_code == 201


async def test_delete_soft_and_tenant_scoped(api, auth, make_tenant):
    ep_id = (await create(api, auth)).json()["id"]
    other = await make_tenant("Чужой")
    assert (await api.client.delete(f"/v1/webhooks/{ep_id}", headers=other)).status_code == 404
    assert (await api.client.get("/v1/webhooks", headers=other)).json() == []
    r = await api.client.delete(f"/v1/webhooks/{ep_id}", headers=auth)
    assert r.status_code == 204
    async with api.sessionmaker() as s:
        ep = (await s.execute(select(WebhookEndpoint))).scalar_one()
    assert ep.active is False
    assert (await api.client.get("/v1/webhooks", headers=auth)).json() == []


@pytest.mark.parametrize("bad", [str(uuid.uuid4()), "zzz"])
async def test_delete_unknown_404(api, auth, bad):
    assert (await api.client.delete(f"/v1/webhooks/{bad}", headers=auth)).status_code == 404


async def test_webhooks_require_auth(api):
    assert (await api.client.get("/v1/webhooks")).status_code == 401


async def test_operator_cannot_manage_webhooks(api, auth):
    ep_id = (await create(api, auth)).json()["id"]
    operator, _ = await session_for(api, auth, role="operator")
    r = await create(api, operator)
    assert r.status_code == 403
    assert r.json()["detail"] == "нужны права владельца"
    r = await api.client.delete(f"/v1/webhooks/{ep_id}", headers=operator)
    assert r.status_code == 403
    assert r.json()["detail"] == "нужны права владельца"
    # список виден любому участнику тенанта, вебхук не тронут
    listed = (await api.client.get("/v1/webhooks", headers=operator)).json()
    assert [h["id"] for h in listed] == [ep_id]


async def test_owner_session_manages_webhooks(api, auth):
    owner, _ = await session_for(api, auth, role="owner")
    r = await create(api, owner)
    assert r.status_code == 201, r.text
    assert (await api.client.delete(f"/v1/webhooks/{r.json()['id']}", headers=owner)).status_code == 204


async def test_api_key_manages_webhooks(api, auth):
    r = await create(api, auth)
    assert r.status_code == 201, r.text
    assert (await api.client.delete(f"/v1/webhooks/{r.json()['id']}", headers=auth)).status_code == 204
