import pytest
from api_helpers import session_for

URL = "https://93.184.216.34/api"
MISSING = "6c0e3b3e-0000-4000-8000-000000000000"


async def make_dest(api, headers):
    r = await api.client.post(
        "/v1/destinations",
        headers=headers,
        json={"kind": "test_fake", "name": "D", "config": {"endpoint": URL}, "secrets": {"token": "t"}},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


def rule(**kw):
    base = {
        "name": "Правило",
        "schema": None,
        "conditions": [],
        "export_to": [],
        "require_approval": False,
        "notify": [],
    }
    base.update(kw)
    return base


async def put(api, headers, rules):
    return await api.client.put("/v1/routing-rules", headers=headers, json={"rules": rules})


async def test_empty_by_default(api, auth):
    r = await api.client.get("/v1/routing-rules", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"rules": [], "updated_at": None}


async def test_put_and_get_roundtrip_keeps_order(api, auth, fake_connector):
    d = await make_dest(api, auth)
    rules = [
        rule(name="Б", schema="invoice", export_to=[d], conditions=[{"field": "total", "op": "gt", "value": 1000}]),
        rule(name="А", notify=[d], require_approval=True, conditions=[{"field": "seller", "op": "not_empty"}]),
    ]
    r = await put(api, auth, rules)
    assert r.status_code == 200, r.text
    got = (await api.client.get("/v1/routing-rules", headers=auth)).json()
    assert [x["name"] for x in got["rules"]] == ["Б", "А"]
    assert got["rules"][0]["conditions"] == [{"field": "total", "op": "gt", "value": 1000.0}]
    assert got["rules"][1]["conditions"][0]["value"] is None
    assert got["rules"][0]["export_to"] == [d]
    assert got["updated_at"]
    # PUT заменяет целиком
    await put(api, auth, [])
    assert (await api.client.get("/v1/routing-rules", headers=auth)).json()["rules"] == []


async def test_unknown_destination_422(api, auth, fake_connector):
    assert (await put(api, auth, [rule(export_to=[MISSING])])).status_code == 422
    assert (await put(api, auth, [rule(notify=[MISSING])])).status_code == 422


async def test_foreign_destination_422(api, auth, make_tenant, fake_connector):
    other = await make_tenant("Другой")
    foreign = await make_dest(api, other)
    assert (await put(api, auth, [rule(export_to=[foreign])])).status_code == 422
    assert (await put(api, auth, [rule(notify=[foreign])])).status_code == 422
    # и сами правила изолированы
    mine = await make_dest(api, auth)
    await put(api, auth, [rule(export_to=[mine])])
    assert (await api.client.get("/v1/routing-rules", headers=other)).json()["rules"] == []


async def test_unknown_operator_422(api, auth):
    r = await put(api, auth, [rule(conditions=[{"field": "total", "op": "like", "value": "x"}])])
    assert r.status_code == 422


@pytest.mark.parametrize("op", ["eq", "ne", "gt", "ge", "lt", "le", "contains", "in_dictionary", "empty", "not_empty"])
async def test_all_operators_accepted(api, auth, op):
    r = await put(api, auth, [rule(conditions=[{"field": "f", "op": op, "value": "x"}])])
    assert r.status_code == 200, r.text


async def test_limits(api, auth):
    assert (await put(api, auth, [rule(name=f"r{i}") for i in range(100)])).status_code == 200
    assert (await put(api, auth, [rule(name=f"r{i}") for i in range(101)])).status_code == 422
    c = [{"field": "f", "op": "empty"}] * 20
    assert (await put(api, auth, [rule(conditions=c)])).status_code == 200
    assert (await put(api, auth, [rule(conditions=c + c[:1])])).status_code == 422


async def test_operator_can_read_not_write(api, auth):
    op, _ = await session_for(api, auth, role="operator")
    assert (await api.client.get("/v1/routing-rules", headers=op)).status_code == 200
    assert (await put(api, op, [])).status_code == 403
