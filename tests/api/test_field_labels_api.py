import uuid

from api_helpers import session_for

from docflow.labels import get_label_set
from docflow.schemas.library import builtin_schemas

URL = "/v1/schemas/invoice_ru"


def _title(body, name):
    return next(f["title"] for f in body["fields"] if f["name"] == name)


async def test_get_labels_rows(api, auth):
    r = await api.client.get(f"{URL}/labels", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    rows = {row["key"]: row for row in body["rows"]}
    vat_title = next(f.title for f in builtin_schemas()["invoice_ru"].fields if f.name == "vat")
    assert rows["vat"]["group"] is None
    assert rows["vat"]["default"] == vat_title
    assert rows["vat"]["label"] == ""
    assert rows["vat"]["type"] == "number"
    assert rows["items.qty"]["group"] == "items"
    assert body["queue_default"] is True
    assert body["queue_fields"] == ["supplier", "total"]
    assert rows["supplier"]["queue"] is True and rows["vat"]["queue"] is False
    assert body["slug"] == "invoice_ru" and body["version"] == 1 and body["title"]


async def test_put_then_schema_shows_labels(api, auth):
    before = (await api.client.get(f"{URL}/json-schema", headers=auth)).json()
    body = {"labels": {"vat": "Налог"}, "queue_fields": ["total"]}
    r = await api.client.put(f"{URL}/labels", headers=auth, json=body)
    assert r.status_code == 200, r.text
    assert r.json()["queue_default"] is False
    assert r.json()["queue_fields"] == ["total"]
    for url in (URL, f"{URL}?version=1"):
        got = await api.client.get(url, headers=auth)
        assert _title(got.json(), "vat") == "Налог"
    assert (await api.client.get(f"{URL}/json-schema", headers=auth)).json() == before


async def test_put_replaces_whole_set(api, auth):
    await api.client.put(f"{URL}/labels", headers=auth, json={"labels": {"vat": "Налог"}, "queue_fields": ["total"]})
    r = await api.client.put(f"{URL}/labels", headers=auth, json={"labels": {}, "queue_fields": []})
    assert r.status_code == 200
    body = r.json()
    assert body["queue_default"] is True
    assert all(row["label"] == "" for row in body["rows"])
    assert _title((await api.client.get(URL, headers=auth)).json(), "vat") != "Налог"


async def test_put_422_field_errors(api, auth):
    r = await api.client.put(f"{URL}/labels", headers=auth, json={"labels": {"vat": "x" * 81}})
    assert r.status_code == 422
    assert r.json()["detail"] == [
        {"loc": ["body", "labels", "vat"], "msg": "не длиннее 80 символов", "type": "value_error"}
    ]


async def test_labels_isolated_between_tenants(api, auth, make_tenant):
    other = await make_tenant("Другая")
    await api.client.put(f"{URL}/labels", headers=auth, json={"labels": {"vat": "Налог"}, "queue_fields": []})
    assert _title((await api.client.get(URL, headers=other)).json(), "vat") != "Налог"
    rows = (await api.client.get(f"{URL}/labels", headers=other)).json()["rows"]
    assert next(row for row in rows if row["key"] == "vat")["label"] == ""


async def test_operator_cannot_put_labels(api, auth):
    operator, _ = await session_for(api, auth, role="operator")
    r = await api.client.put(f"{URL}/labels", headers=operator, json={"labels": {}, "queue_fields": []})
    assert r.status_code == 403
    assert (await api.client.get(f"{URL}/labels", headers=operator)).status_code == 200


async def test_labels_unknown_schema_404(api, auth):
    assert (await api.client.get("/v1/schemas/nope/labels", headers=auth)).status_code == 404
    r = await api.client.put("/v1/schemas/nope/labels", headers=auth, json={"labels": {}, "queue_fields": []})
    assert r.status_code == 404


async def test_get_label_set_returns_fresh_empty(api):
    tenant = uuid.uuid4()
    async with api.sessionmaker() as s:
        first = await get_label_set(s, tenant, "invoice_ru")
        first.labels["x"] = "y"
        first.queue_fields.append("z")
        second = await get_label_set(s, tenant, "invoice_ru")
    assert second.labels == {} and second.queue_fields == []


async def test_put_caps_body_size(api, auth):
    r = await api.client.put(f"{URL}/labels", headers=auth, json={"labels": {}, "queue_fields": ["a"] * 51})
    assert r.status_code == 422
    big = {f"k{i}": "x" for i in range(501)}
    r = await api.client.put(f"{URL}/labels", headers=auth, json={"labels": big, "queue_fields": []})
    assert r.status_code == 422


async def test_get_schema_raw_skips_labels(api, auth):
    base = (await api.client.get(f"{URL}?raw=true", headers=auth)).json()
    await api.client.put(f"{URL}/labels", headers=auth, json={"labels": {"vat": "Налог"}})
    assert _title((await api.client.get(URL, headers=auth)).json(), "vat") == "Налог"
    raw = (await api.client.get(f"{URL}?raw=true", headers=auth)).json()
    assert raw == base and _title(raw, "vat") != "Налог"
