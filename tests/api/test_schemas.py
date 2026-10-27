import pytest

from docflow.schemas.library import builtin_schemas

MY_SCHEMA = {
    "slug": "my_doc",
    "version": 99,
    "title": "Мой документ",
    "description": "Тестовая схема",
    "fields": [{"name": "number", "type": "string", "description": "номер"}],
}


async def test_list_includes_builtins(api, auth):
    r = await api.client.get("/v1/schemas", headers=auth)
    assert r.status_code == 200
    items = {i["slug"]: i for i in r.json()}
    for slug, schema in builtin_schemas().items():
        assert items[slug] == {"slug": slug, "version": schema.version, "title": schema.title, "builtin": True}


async def test_list_requires_auth(api):
    assert (await api.client.get("/v1/schemas")).status_code == 401


async def test_post_twice_gives_versions_1_and_2(api, auth):
    r1 = await api.client.post("/v1/schemas", headers=auth, json=MY_SCHEMA)
    r2 = await api.client.post("/v1/schemas", headers=auth, json={**MY_SCHEMA, "title": "Мой документ v2"})
    assert (r1.status_code, r2.status_code) == (201, 201)
    assert r1.json()["version"] == 1
    assert r2.json()["version"] == 2
    items = {i["slug"]: i for i in (await api.client.get("/v1/schemas", headers=auth)).json()}
    assert items["my_doc"] == {"slug": "my_doc", "version": 2, "title": "Мой документ v2", "builtin": False}
    latest = (await api.client.get("/v1/schemas/my_doc", headers=auth)).json()
    assert latest["version"] == 2
    assert latest["title"] == "Мой документ v2"
    v1 = (await api.client.get("/v1/schemas/my_doc", params={"version": 1}, headers=auth)).json()
    assert v1["title"] == "Мой документ"


async def test_post_invalid_schema_422(api, auth):
    bad = {**MY_SCHEMA, "fields": [{"name": "x", "type": "string", "description": "d", "rules": [{"kind": "nope"}]}]}
    r = await api.client.post("/v1/schemas", headers=auth, json=bad)
    assert r.status_code == 422
    assert "detail" in r.json()
    r = await api.client.post("/v1/schemas", headers=auth, json={"slug": "BAD SLUG"})
    assert r.status_code == 422


async def test_tenant_schema_shadows_builtin(api, auth, make_tenant):
    override = {**MY_SCHEMA, "slug": "invoice_ru", "title": "Наш счёт"}
    assert (await api.client.post("/v1/schemas", headers=auth, json=override)).status_code == 201
    items = {i["slug"]: i for i in (await api.client.get("/v1/schemas", headers=auth)).json()}
    # версии своей схемы идут после встроенной: перекрытие встроенной v1 — v2
    builtin_version = builtin_schemas()["invoice_ru"].version
    assert items["invoice_ru"] == {
        "slug": "invoice_ru", "version": builtin_version + 1, "title": "Наш счёт", "builtin": False
    }
    # другой тенант по-прежнему видит встроенную
    other = await make_tenant("Другой")
    items = {i["slug"]: i for i in (await api.client.get("/v1/schemas", headers=other)).json()}
    assert items["invoice_ru"]["builtin"] is True


async def test_other_tenant_schema_invisible(api, auth, make_tenant):
    await api.client.post("/v1/schemas", headers=auth, json=MY_SCHEMA)
    other = await make_tenant("Другой")
    assert (await api.client.get("/v1/schemas/my_doc", headers=other)).status_code == 404
    slugs = {i["slug"] for i in (await api.client.get("/v1/schemas", headers=other)).json()}
    assert "my_doc" not in slugs


async def test_get_builtin_and_json_schema(api, auth):
    r = await api.client.get("/v1/schemas/invoice_ru", headers=auth)
    assert r.status_code == 200
    assert r.json()["slug"] == "invoice_ru"
    js = await api.client.get("/v1/schemas/invoice_ru/json-schema", headers=auth)
    assert js.status_code == 200
    assert js.json()["type"] == "object"
    assert "number" in js.json()["properties"]


@pytest.mark.parametrize("path", ["/v1/schemas/nope", "/v1/schemas/nope/json-schema"])
async def test_unknown_schema_404(api, auth, path):
    r = await api.client.get(path, headers=auth)
    assert r.status_code == 404
    assert "detail" in r.json()


async def test_override_version_follows_builtin_and_v1_stays_builtin(api, auth):
    builtin = builtin_schemas()["invoice_ru"]
    override = {**MY_SCHEMA, "slug": "invoice_ru", "title": "Наш счёт"}
    r = await api.client.post("/v1/schemas", headers=auth, json=override)
    assert r.json()["version"] == builtin.version + 1
    r = await api.client.post("/v1/schemas", headers=auth, json={**override, "title": "Наш счёт 2"})
    assert r.json()["version"] == builtin.version + 2
    # явная версия встроенной находит встроенную, хотя у тенанта есть своя схема с этим slug
    pinned = (await api.client.get("/v1/schemas/invoice_ru", params={"version": builtin.version}, headers=auth)).json()
    assert pinned["title"] == builtin.title
    latest = (await api.client.get("/v1/schemas/invoice_ru", headers=auth)).json()
    assert latest["title"] == "Наш счёт 2"


async def test_override_version_follows_builtin_seeded_in_db(api, auth):
    """Встроенная версия, засеянная в БД новее библиотечной, тоже учитывается."""
    from docflow.db.models import SchemaRecord

    builtin = builtin_schemas()["invoice_ru"]
    seeded = builtin.model_copy(update={"version": builtin.version + 4, "title": "Встроенный v+4"})
    async with api.sessionmaker() as s:
        body = seeded.model_dump(mode="json")
        s.add(SchemaRecord(tenant_id=None, slug="invoice_ru", version=seeded.version, body=body))
        await s.commit()
    override = {**MY_SCHEMA, "slug": "invoice_ru", "title": "Наш счёт"}
    r = await api.client.post("/v1/schemas", headers=auth, json=override)
    assert r.json()["version"] == seeded.version + 1
