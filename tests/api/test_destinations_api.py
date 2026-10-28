import uuid

from api_helpers import session_for
from sqlalchemy import select

from docflow.connectors.base import ConnectorError
from docflow.db.models import Destination
from docflow.secrets import decrypt_secrets

URL = "https://93.184.216.34/api"


def body(**kw):
    base = {
        "kind": "test_fake",
        "name": "Моя система",
        "config": {"endpoint": URL},
        "secrets": {"token": "SUPER-SECRET"},
        "mapping": {"total": "amount"},
    }
    base.update(kw)
    return base


async def create(api, headers, **kw):
    return await api.client.post("/v1/destinations", headers=headers, json=body(**kw))


async def test_create_hides_secrets_and_encrypts(api, auth, fake_connector):
    r = await create(api, auth)
    assert r.status_code == 201, r.text
    d = r.json()
    assert d["has_secrets"] is True
    assert d["kind"] == "test_fake" and d["active"] is True
    assert d["config"] == {"endpoint": URL} and d["mapping"] == {"total": "amount"}
    assert "SUPER-SECRET" not in r.text and "secrets" not in d
    listed = await api.client.get("/v1/destinations", headers=auth)
    assert "SUPER-SECRET" not in listed.text
    assert [x["id"] for x in listed.json()] == [d["id"]]
    async with api.sessionmaker() as s:
        row = await s.get(Destination, uuid.UUID(d["id"]))
    assert "SUPER-SECRET" not in row.secrets_enc
    assert decrypt_secrets(row.secrets_enc, api.settings.secrets_key) == {"token": "SUPER-SECRET"}


async def test_unknown_kind_422(api, auth, fake_connector):
    r = await create(api, auth, kind="nope")
    assert r.status_code == 422


async def test_missing_required_fields_422(api, auth, fake_connector):
    assert (await create(api, auth, config={})).status_code == 422
    assert (await create(api, auth, secrets={})).status_code == 422


async def test_private_url_422_and_allowed_in_dev(api, auth, fake_connector):
    private = {"endpoint": "http://127.0.0.1/x"}
    assert (await create(api, auth, config=private)).status_code == 422
    api.settings.webhook_allow_private = True
    assert (await create(api, auth, config=private)).status_code == 201


async def test_non_string_mapping_422(api, auth, fake_connector):
    assert (await create(api, auth, mapping={"a": 1})).status_code == 422


async def test_no_secrets_key_refuses_secrets(api, auth, fake_connector):
    api.settings.secrets_key = ""
    r = await create(api, auth)
    assert r.status_code == 422
    assert "ключ шифрования" in r.json()["detail"]


async def test_patch_without_secrets_keeps_them(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    r = await api.client.patch(f"/v1/destinations/{d['id']}", headers=auth, json={"name": "Новое", "active": False})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "Новое" and r.json()["active"] is False and r.json()["has_secrets"] is True
    async with api.sessionmaker() as s:
        row = await s.get(Destination, uuid.UUID(d["id"]))
    assert decrypt_secrets(row.secrets_enc, api.settings.secrets_key) == {"token": "SUPER-SECRET"}
    r = await api.client.patch(f"/v1/destinations/{d['id']}", headers=auth, json={"secrets": {"token": "NEW"}})
    assert r.status_code == 200
    async with api.sessionmaker() as s:
        row = await s.get(Destination, uuid.UUID(d["id"]))
    assert decrypt_secrets(row.secrets_enc, api.settings.secrets_key) == {"token": "NEW"}


async def test_patch_validates_config(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    r = await api.client.patch(f"/v1/destinations/{d['id']}", headers=auth, json={"config": {"endpoint": "ftp://x"}})
    assert r.status_code == 422
    r = await api.client.patch(f"/v1/destinations/{d['id']}", headers=auth, json={"config": {}})
    assert r.status_code == 422


async def test_get_one_and_delete(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    assert (await api.client.get(f"/v1/destinations/{d['id']}", headers=auth)).json()["id"] == d["id"]
    assert (await api.client.delete(f"/v1/destinations/{d['id']}", headers=auth)).status_code == 204
    assert (await api.client.get(f"/v1/destinations/{d['id']}", headers=auth)).status_code == 404
    assert (await api.client.get("/v1/destinations/not-a-uuid", headers=auth)).status_code == 404
    async with api.sessionmaker() as s:
        assert (await s.execute(select(Destination))).first() is None


async def test_tenant_isolation(api, auth, make_tenant, fake_connector):
    d = (await create(api, auth)).json()
    other = await make_tenant("Другой")
    assert (await api.client.get("/v1/destinations", headers=other)).json() == []
    url = f"/v1/destinations/{d['id']}"
    assert (await api.client.get(url, headers=other)).status_code == 404
    assert (await api.client.patch(url, headers=other, json={"name": "x"})).status_code == 404
    assert (await api.client.delete(url, headers=other)).status_code == 404
    assert (await api.client.post(f"{url}/test", headers=other)).status_code == 404


async def test_operator_read_only(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    op, _ = await session_for(api, auth, role="operator")
    assert (await api.client.get("/v1/destinations", headers=op)).status_code == 200
    assert (await api.client.get(f"/v1/destinations/{d['id']}", headers=op)).status_code == 200
    assert (await create(api, op)).status_code == 403
    assert (await api.client.patch(f"/v1/destinations/{d['id']}", headers=op, json={"name": "x"})).status_code == 403
    assert (await api.client.delete(f"/v1/destinations/{d['id']}", headers=op)).status_code == 403
    assert (await api.client.post(f"/v1/destinations/{d['id']}/test", headers=op)).status_code == 403
    owner, _ = await session_for(api, auth, role="owner")
    assert (await create(api, owner)).status_code == 201


async def test_test_endpoint_calls_connector(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    r = await api.client.post(f"/v1/destinations/{d['id']}/test", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "Всё хорошо"}
    assert fake_connector.calls == [("test", {"endpoint": URL}, {"token": "SUPER-SECRET"})]


async def test_test_endpoint_connector_error_is_ok_false(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    fake_connector.error = ConnectorError("Неверный токен", retryable=False)
    r = await api.client.post(f"/v1/destinations/{d['id']}/test", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"ok": False, "message": "Неверный токен"}


async def test_test_endpoint_unexpected_error_is_ok_false(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    fake_connector.error = RuntimeError("boom SUPER-SECRET")
    r = await api.client.post(f"/v1/destinations/{d['id']}/test", headers=auth)
    assert r.status_code == 200
    assert r.json()["ok"] is False and "SUPER-SECRET" not in r.text


async def test_test_endpoint_without_key_for_secrets(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    api.settings.secrets_key = ""
    r = await api.client.post(f"/v1/destinations/{d['id']}/test", headers=auth)
    assert r.status_code == 422
    assert "ключ шифрования" in r.json()["detail"]


async def test_connectors_catalog(api, auth, fake_connector):
    r = await api.client.get("/v1/connectors", headers=auth)
    assert r.status_code == 200
    item = next(c for c in r.json() if c["kind"] == "test_fake")
    assert item["label"] == "Тестовый"
    assert item["config_fields"][0] == {
        "name": "endpoint",
        "label": "Адрес",
        "required": True,
        "help": "",
        "type": "url",
        "options": None,
    }
    assert item["secret_fields"][0]["name"] == "token"
    assert item["target_fields"][0]["name"] == "amount"
    by_kind = {c["kind"]: c for c in r.json()}
    # Телеграм умеет только уведомления, Google Таблицы — только выгрузку
    assert (by_kind["telegram"]["can_export"], by_kind["telegram"]["can_notify"]) == (False, True)
    assert (by_kind["google_sheets"]["can_export"], by_kind["google_sheets"]["can_notify"]) == (True, False)
    assert (await api.client.get("/v1/connectors")).status_code == 401


async def test_delete_keeps_export_history(api, auth, fake_connector):
    from docflow.db.models import Document, Export

    d = (await create(api, auth)).json()
    async with api.sessionmaker() as s:
        from docflow.db.models import ApiKey, hash_key

        tenant_id = (
            await s.execute(select(ApiKey.tenant_id).where(ApiKey.key_hash == hash_key(auth["Authorization"][7:])))
        ).scalar_one()
        doc = Document(tenant_id=tenant_id, schema_slug="invoice", schema_version=1, files=[], mode="scan")
        s.add(doc)
        await s.flush()
        exp = Export(tenant_id=tenant_id, document_id=doc.id, destination_id=uuid.UUID(d["id"]))
        s.add(exp)
        await s.commit()
        exp_id = exp.id
    assert (await api.client.delete(f"/v1/destinations/{d['id']}", headers=auth)).status_code == 204
    async with api.sessionmaker() as s:
        kept = await s.get(Export, exp_id)
    assert kept is not None and kept.destination_id is None


async def test_secret_url_private_rejected_everywhere(api, auth, fake_connector):
    bad = "http://127.0.0.1/rest/1/KEY/"
    r = await create(api, auth, secrets={"token": "t", "hook": bad})
    assert r.status_code == 422 and "KEY" not in r.text
    d = (await create(api, auth, secrets={"token": "t", "hook": URL})).json()
    r = await api.client.patch(f"/v1/destinations/{d['id']}", headers=auth, json={"secrets": {"hook": bad}})
    assert r.status_code == 422 and "KEY" not in r.text
    # адрес в секрете мог попасть в БД до проверки или смена DNS: /test проверяет заново
    api.settings.webhook_allow_private = True
    p = (await create(api, auth, secrets={"token": "t", "hook": bad})).json()
    api.settings.webhook_allow_private = False
    r = await api.client.post(f"/v1/destinations/{p['id']}/test", headers=auth)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert "KEY" not in r.text and fake_connector.calls == []


async def test_destctx_repr_hides_secrets():
    from docflow.connectors.base import DestCtx

    ctx = DestCtx(config={"a": 1}, secrets={"token": "TOPSECRET"}, mapping={}, http=None)
    assert "TOPSECRET" not in repr(ctx) and "TOPSECRET" not in str(ctx)


async def test_patch_recovers_after_key_change(api, auth, fake_connector):
    from cryptography.fernet import Fernet

    d = (await create(api, auth)).json()
    api.settings.secrets_key = Fernet.generate_key().decode()
    url = f"/v1/destinations/{d['id']}"
    # частичный набор секретов при нечитаемых старых: отказ
    r = await api.client.patch(url, headers=auth, json={"secrets": {"hook": URL}})
    assert r.status_code == 422
    # полный набор обязательных: перешифровываем текущим ключом
    r = await api.client.patch(url, headers=auth, json={"secrets": {"token": "FRESH"}})
    assert r.status_code == 200, r.text
    async with api.sessionmaker() as s:
        row = await s.get(Destination, uuid.UUID(d["id"]))
    assert decrypt_secrets(row.secrets_enc, api.settings.secrets_key) == {"token": "FRESH"}


async def test_delete_prunes_routing_rules(api, auth, fake_connector):
    a = (await create(api, auth)).json()["id"]
    b = (await create(api, auth)).json()["id"]
    rules = [
        {"name": "r1", "export_to": [a, b], "notify": [a]},
        {"name": "r2", "export_to": [b], "notify": []},
    ]
    assert (await api.client.put("/v1/routing-rules", headers=auth, json={"rules": rules})).status_code == 200
    assert (await api.client.delete(f"/v1/destinations/{a}", headers=auth)).status_code == 204
    got = (await api.client.get("/v1/routing-rules", headers=auth)).json()["rules"]
    assert got[0]["export_to"] == [b] and got[0]["notify"] == []
    assert got[1]["export_to"] == [b]
