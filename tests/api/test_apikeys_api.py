from api_helpers import session_for


def _bearer(key):
    return {"Authorization": f"Bearer {key}"}


async def test_apikeys_owner_crud(api, auth):
    owner, _ = await session_for(api, auth, role="owner")
    listed = (await api.client.get("/v1/api-keys", headers=owner)).json()
    assert len(listed) == 1  # ключ из фикстуры
    assert set(listed[0]) == {"id", "prefix", "created_at", "revoked_at"}

    r = await api.client.post("/v1/api-keys", headers=owner)
    assert r.status_code == 201, r.text
    created = r.json()
    assert created["key"].startswith("dfk_") and created["key"].startswith(created["prefix"])
    assert set(created) == {"id", "prefix", "key", "created_at"}
    assert (await api.client.get("/v1/webhooks", headers=_bearer(created["key"]))).status_code == 200
    assert all("key" not in k for k in (await api.client.get("/v1/api-keys", headers=owner)).json())

    assert (await api.client.delete(f"/v1/api-keys/{created['id']}", headers=owner)).status_code == 204
    after = {k["id"]: k for k in (await api.client.get("/v1/api-keys", headers=owner)).json()}
    assert after[created["id"]]["revoked_at"] is not None


async def test_apikeys_operator_403(api, auth):
    operator, _ = await session_for(api, auth, role="operator")
    assert (await api.client.get("/v1/api-keys", headers=operator)).status_code == 403
    assert (await api.client.post("/v1/api-keys", headers=operator)).status_code == 403


async def test_apikeys_key_auth_403(api, auth):
    assert (await api.client.get("/v1/api-keys", headers=auth)).status_code == 403
    assert (await api.client.post("/v1/api-keys", headers=auth)).status_code == 403


async def test_revoked_key_stops_working(api, auth):
    owner, _ = await session_for(api, auth)
    key = (await api.client.post("/v1/api-keys", headers=owner)).json()
    assert (await api.client.get("/v1/webhooks", headers=_bearer(key["key"]))).status_code == 200
    await api.client.delete(f"/v1/api-keys/{key['id']}", headers=owner)
    assert (await api.client.get("/v1/webhooks", headers=_bearer(key["key"]))).status_code == 401


async def test_apikeys_foreign_404(api, auth, make_tenant):
    other = await make_tenant("Чужой")
    other_owner, _ = await session_for(api, other)
    foreign_id = (await api.client.get("/v1/api-keys", headers=other_owner)).json()[0]["id"]
    owner, _ = await session_for(api, auth)
    assert (await api.client.delete(f"/v1/api-keys/{foreign_id}", headers=owner)).status_code == 404
    assert (await api.client.delete("/v1/api-keys/не-id", headers=owner)).status_code == 404
    assert (await api.client.get("/v1/webhooks", headers=other)).status_code == 200  # ключ чужого жив
