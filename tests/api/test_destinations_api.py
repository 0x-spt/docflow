import uuid

from api_helpers import session_for
from sqlalchemy import select

from docflow.connectors import base as connectors_base
from docflow.connectors.base import ConnectorError
from docflow.db.models import Destination
from docflow.schemas.library import builtin_schemas
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
        "option_labels": None,
        "advanced": False,
    }
    assert item["secret_fields"][0]["name"] == "token"
    assert item["target_fields"][0]["name"] == "amount"
    by_kind = {c["kind"]: c for c in r.json()}
    # Телеграм умеет только уведомления, Google Таблицы — только выгрузку
    assert (by_kind["telegram"]["can_export"], by_kind["telegram"]["can_notify"]) == (False, True)
    assert (by_kind["google_sheets"]["can_export"], by_kind["google_sheets"]["can_notify"]) == (True, False)
    assert (await api.client.get("/v1/connectors")).status_code == 401


async def test_catalog_descriptions_and_option_labels(api, auth):
    by_kind = {c["kind"]: c for c in (await api.client.get("/v1/connectors", headers=auth)).json()}
    assert by_kind["onec_odata"]["description"] == "поступления и контрагенты по ИНН"
    assert by_kind["google_sheets"]["description"] == "строка на каждый документ"
    assert all(by_kind[k]["description"] for k in ("bitrix24", "amocrm", "telegram", "email"))
    fields = {f["name"]: f for f in by_kind["onec_odata"]["config_fields"]}
    assert fields["document_kind"]["option_labels"]["purchase"] == "Поступление (входящие документы)"
    assert fields["vat_rate"]["option_labels"]["НДС22"] == "НДС 22%"
    assert set(fields["vat_rate"]["option_labels"]) == set(fields["vat_rate"]["options"])
    advanced = {n for n, f in fields.items() if f["advanced"]}
    assert advanced == {
        "contract_key", "currency_key", "operation_kind", "document_object", "counterparty_object",
        "nomenclature_object", "field_names",
    }


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


def remote_url(d, schema="invoice_ru"):
    return f"/v1/destinations/{d['id']}/remote-fields?schema={schema}"


async def test_remote_fields_returns_fields_and_suggestions(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["fields"] == [{"code": "amount", "title": "Сумма"}, {"code": "tax", "title": "НДС"}]
    # total уже сопоставлен, amount занят
    assert r.json()["suggested"] == {"vat": "tax"}
    assert fake_connector.calls == [("remote_fields", {"endpoint": URL}, {"token": "SUPER-SECRET"})]
    assert "SUPER-SECRET" not in r.text


async def test_remote_fields_keeps_saved_mapping(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    assert (await api.client.post(remote_url(d), headers=auth)).status_code == 200
    got = await api.client.get(f"/v1/destinations/{d['id']}", headers=auth)
    assert got.json()["mapping"] == {"total": "amount"}
    assert got.json()["updated_at"] == d["updated_at"]


async def test_remote_fields_uses_company_labels(api, auth, fake_connector):
    put = await api.client.put("/v1/schemas/invoice_ru/labels", headers=auth, json={"labels": {"subtotal": "Налог"}})
    assert put.status_code == 200, put.text
    fake_connector.remote = [connectors_base.RemoteField("n1", "Налог")]
    d = (await create(api, auth, mapping={})).json()
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.json()["suggested"] == {"subtotal": "n1"}


async def test_remote_fields_connector_error_502(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    fake_connector.error = ConnectorError("Неверный токен")
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.status_code == 502
    assert r.json()["detail"] == "Неверный токен"


async def test_remote_fields_unexpected_error_hides_secret(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    fake_connector.error = RuntimeError("boom SUPER-SECRET")
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.status_code == 502
    assert "SUPER-SECRET" not in r.text and "RuntimeError" in r.json()["detail"]


async def test_remote_fields_not_supported_422(api, auth, fake_notifier):
    d = (await create(api, auth, kind="test_notifier", config={}, secrets={}, mapping={})).json()
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.status_code == 422
    assert r.json()["detail"] == "«Тестовые уведомления» не отдаёт список полей"


async def test_remote_fields_operator_403_and_foreign_404(api, auth, make_tenant, fake_connector):
    d = (await create(api, auth)).json()
    op, _ = await session_for(api, auth, role="operator")
    assert (await api.client.post(remote_url(d), headers=op)).status_code == 403
    other = await make_tenant("Другой")
    assert (await api.client.post(remote_url(d), headers=other)).status_code == 404
    assert fake_connector.calls == []


async def test_remote_fields_unknown_schema_404(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    r = await api.client.post(remote_url(d, schema="nope_schema"), headers=auth)
    assert r.status_code == 404
    assert fake_connector.calls == []


async def test_remote_fields_taken_code_not_suggested(api, auth, fake_connector):
    # amount уже у supplier: «Сумма» не предлагается в total, хотя total свободен
    d = (await create(api, auth, mapping={"supplier": "amount"})).json()
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["suggested"] == {"vat": "tax"}


async def test_remote_fields_own_suggestion_respects_saved_mapping(api, auth, fake_connector):
    # как у 1С: свой подбор doc_field → роль; роль counterparty уже занята buyer, ключ buyer сопоставлен
    fake_connector.suggest_mapping = lambda schema, config: {
        "supplier": "counterparty",
        "supplier_inn": "counterparty_inn",
        "buyer": "item_name",
    }
    d = (await create(api, auth, mapping={"buyer": "counterparty"})).json()
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["suggested"] == {"supplier_inn": "counterparty_inn"}
    assert "counterparty" not in r.json()["suggested"].values()


async def _set(api, d, **values):
    async with api.sessionmaker() as s:
        row = await s.get(Destination, uuid.UUID(d["id"]))
        for k, v in values.items():
            setattr(row, k, v)
        await s.commit()


async def test_remote_fields_unknown_kind_422(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    await _set(api, d, kind="gone_kind")
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.status_code == 422
    assert r.json()["detail"] == "«gone_kind» не отдаёт список полей"


async def test_remote_fields_private_url_422(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    await _set(api, d, config={"endpoint": "http://127.0.0.1/x"})
    assert (await api.client.post(remote_url(d), headers=auth)).status_code == 422
    assert fake_connector.calls == []


async def test_remote_fields_without_secrets_key_422(api, auth, fake_connector):
    d = (await create(api, auth)).json()
    api.settings.secrets_key = ""
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.status_code == 422
    assert "ключ шифрования" in r.json()["detail"]
    assert fake_connector.calls == []


async def test_remote_fields_own_names_not_suggested_for_header_match(api, auth, fake_connector):
    # заголовок таблицы = наше название поля: находится и так, в сопоставление его не записываем
    fake_connector.match_by_header = True
    number = next(f.title for f in builtin_schemas()["invoice_ru"].fields if f.name == "number")
    fake_connector.remote = [connectors_base.RemoteField(t, t) for t in (number, "Налог")]
    await api.client.put("/v1/schemas/invoice_ru/labels", headers=auth, json={"labels": {"vat": "Налог"}})
    d = (await create(api, auth, mapping={})).json()
    r = await api.client.post(remote_url(d), headers=auth)
    assert r.json()["suggested"] == {}
