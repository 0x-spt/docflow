import pytest

CLINICS = {
    "title": "Клиники",
    "entries": [{"value": "Дента-Люкс"}, {"value": "Стомат-Сервис", "aliases": ["Стоматсервис"]}],
}


async def put(api, headers, name="clinics", body=CLINICS):
    return await api.client.put(f"/v1/dictionaries/{name}", headers=headers, json=body)


async def test_put_creates_then_replaces(api, auth):
    r = await put(api, auth)
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "clinics" and len(r.json()["entries"]) == 2
    got = (await api.client.get("/v1/dictionaries/clinics", headers=auth)).json()
    assert got["title"] == "Клиники"
    assert got["entries"][1] == {"value": "Стомат-Сервис", "aliases": ["Стоматсервис"]}
    assert got["entries"][0] == {"value": "Дента-Люкс", "aliases": []}

    r = await put(api, auth, body={"title": "Клиники-партнёры", "entries": [{"value": "Улыбка+"}]})
    assert r.status_code == 200
    got = (await api.client.get("/v1/dictionaries/clinics", headers=auth)).json()
    assert got["title"] == "Клиники-партнёры"
    assert [e["value"] for e in got["entries"]] == ["Улыбка+"]


async def test_list_shows_sizes(api, auth):
    await put(api, auth)
    await put(api, auth, name="doctors", body={"title": "Врачи", "entries": [{"value": "Ким Д.О."}]})
    listed = (await api.client.get("/v1/dictionaries", headers=auth)).json()
    assert [(d["name"], d["size"]) for d in listed] == [("clinics", 2), ("doctors", 1)]
    assert "entries" not in listed[0]


async def test_delete(api, auth):
    await put(api, auth)
    assert (await api.client.delete("/v1/dictionaries/clinics", headers=auth)).status_code == 204
    assert (await api.client.get("/v1/dictionaries/clinics", headers=auth)).status_code == 404
    assert (await api.client.delete("/v1/dictionaries/clinics", headers=auth)).status_code == 404


async def test_isolated_between_tenants(api, auth, make_tenant):
    other = await make_tenant("Другой")
    await put(api, auth)
    assert (await api.client.get("/v1/dictionaries/clinics", headers=other)).status_code == 404
    assert (await api.client.get("/v1/dictionaries", headers=other)).json() == []
    assert (await api.client.delete("/v1/dictionaries/clinics", headers=other)).status_code == 404
    # у другого клиента может быть свой справочник с тем же именем
    assert (await put(api, other, body={"title": "Свои", "entries": [{"value": "X"}]})).status_code == 200
    assert len((await api.client.get("/v1/dictionaries/clinics", headers=auth)).json()["entries"]) == 2


@pytest.mark.parametrize(
    "name, body",
    [
        ("Clinics", CLINICS),  # имя — как slug схемы
        ("a", CLINICS),
        ("clinics", {"title": "T", "entries": [{"value": ""}]}),
        ("clinics", {"title": "T", "entries": [{"value": "  "}]}),
        ("clinics", {"title": "T", "entries": [{"value": "A"}, {"value": "a"}]}),  # дубль после нормализации
        ("clinics", {"title": "T", "entries": [{"value": "A"}, {"value": "B", "aliases": ["a"]}]}),
        ("clinics", {"title": "T", "entries": [{"value": "x" * 201}]}),
        ("clinics", {"title": "T", "entries": [{"value": f"v{i}"} for i in range(5001)]}),
    ],
)
async def test_invalid_422(api, auth, name, body):
    r = await put(api, auth, name=name, body=body)
    assert r.status_code == 422, r.text


async def test_requires_auth(api):
    assert (await api.client.get("/v1/dictionaries")).status_code == 401
