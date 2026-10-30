"""Выгрузки в API: одобрение → выгрузка, журнал, «Повторить», блок exports в документе."""

import uuid
from datetime import UTC, datetime, timedelta

from api_helpers import session_for
from sqlalchemy import select
from test_documents import add_doc
from test_review_api import add_review_doc, review

from docflow.db.models import ApiKey, BotChat, Destination, Export, RoutingRules, TenantBot, hash_key
from docflow.exports import export_job_id
from docflow.queue import DELIVER_EXPORT


async def tenant_of(api, headers) -> uuid.UUID:
    async with api.sessionmaker() as s:
        return (
            await s.execute(select(ApiKey.tenant_id).where(ApiKey.key_hash == hash_key(headers["Authorization"][7:])))
        ).scalar_one()


async def add_dest(api, headers, name="1С бухгалтерия", kind="test_fake") -> uuid.UUID:
    tenant_id = await tenant_of(api, headers)
    async with api.sessionmaker() as s:
        d = Destination(tenant_id=tenant_id, kind=kind, name=name, config={}, mapping={})
        s.add(d)
        await s.commit()
        return d.id


async def set_rules(api, headers, rules):
    tenant_id = await tenant_of(api, headers)
    async with api.sessionmaker() as s:
        row = await s.get(RoutingRules, tenant_id)
        stored = [
            {"name": "п", "schema": None, "conditions": [], "export_to": [], "notify": [], "require_approval": False}
            | r
            for r in rules
        ]
        if row is None:
            s.add(RoutingRules(tenant_id=tenant_id, rules=stored))
        else:
            row.rules = stored
        await s.commit()


async def add_export(api, headers, doc_id, dest_id, **values) -> uuid.UUID:
    tenant_id = await tenant_of(api, headers)
    async with api.sessionmaker() as s:
        e = Export(tenant_id=tenant_id, document_id=uuid.UUID(doc_id), destination_id=dest_id, **values)
        s.add(e)
        await s.commit()
        return e.id


async def rows(api, doc_id):
    async with api.sessionmaker() as s:
        found = (await s.execute(select(Export).where(Export.document_id == uuid.UUID(doc_id)))).scalars().all()
        return sorted((r.destination_id, r.purpose, r.status) for r in found)


def export_calls(api):
    return [c for c in api.enqueuer.calls if c[0] == DELIVER_EXPORT]


# --- одобрение -------------------------------------------------------------


async def test_approve_exports_and_announces_once(api, auth, fake_connector):
    sys_id = await add_dest(api, auth)
    ntf_id = await add_dest(api, auth, name="Бухгалтер")
    await set_rules(api, auth, [{"export_to": [str(sys_id)], "notify": [str(ntf_id)]}])
    doc_id = await add_review_doc(api, auth)

    r = await review(api, auth, doc_id)
    assert r.status_code == 200, r.text
    assert await rows(api, doc_id) == sorted([(sys_id, "export", "pending"), (ntf_id, "notify_new", "pending")])
    assert len(export_calls(api)) == 2
    body = r.json()
    assert sorted((e["purpose"], e["status"]) for e in body["exports"]) == [("export", "pending"),
                                                                            ("notify_new", "pending")]

    # повторное одобрение — 409 и без дублей
    assert (await review(api, auth, doc_id)).status_code == 409
    assert len(await rows(api, doc_id)) == 2 and len(export_calls(api)) == 2


async def test_approve_satisfies_approval_rule(api, auth, fake_connector):
    sys_id = await add_dest(api, auth)
    await set_rules(api, auth, [{"name": "Крупные", "require_approval": True, "export_to": [str(sys_id)]}])
    doc_id = await add_review_doc(
        api, auth, issues=[{"field": None, "code": "approval_required", "message": "нужно согласование: Крупные"}]
    )
    r = await review(api, auth, doc_id)
    assert r.status_code == 200 and r.json()["status"] == "ok"
    assert await rows(api, doc_id) == [(sys_id, "export", "pending")]


async def test_approve_with_edits_routes_by_edited_data(api, auth, fake_connector):
    big = await add_dest(api, auth, name="Крупные")
    small = await add_dest(api, auth, name="Мелкие")
    await set_rules(
        api,
        auth,
        [
            {"conditions": [{"field": "total", "op": "gt", "value": 1000}], "export_to": [str(big)]},
            {"conditions": [{"field": "total", "op": "le", "value": 1000}], "export_to": [str(small)]},
        ],
    )
    doc_id = await add_review_doc(api, auth)  # total 100
    r = await review(api, auth, doc_id, data={"issued": "2026-10-20", "delivery": "2026-10-22", "total": "5 000"})
    assert r.status_code == 200, r.text
    assert await rows(api, doc_id) == [(big, "export", "pending")]


async def test_reject_routes_nothing(api, auth, fake_connector):
    sys_id = await add_dest(api, auth)
    await set_rules(api, auth, [{"export_to": [str(sys_id)], "notify": [str(sys_id)]}])
    doc_id = await add_review_doc(api, auth)
    assert (await review(api, auth, doc_id, decision="reject")).status_code == 200
    assert await rows(api, doc_id) == [] and export_calls(api) == []


# --- журнал ---------------------------------------------------------------


async def test_list_exports_filters_and_isolation(api, auth, make_tenant, fake_connector):
    dest = await add_dest(api, auth)
    d1 = await add_doc(api, auth)
    d2 = await add_doc(api, auth)
    base = datetime(2026, 10, 1, tzinfo=UTC)
    e1 = await add_export(api, auth, d1, dest, status="done", external_id="42", created_at=base)
    e2 = await add_export(api, auth, d2, dest, status="failed", last_error="нет связи",
                          created_at=base + timedelta(hours=1))
    e3 = await add_export(api, auth, d2, None, purpose="notify_new", status="failed", last_error=DEST_OFF_TEXT,
                          created_at=base + timedelta(hours=2))
    other = await make_tenant("Чужой")
    od = await add_doc(api, other)
    await add_export(api, other, od, None)

    r = await api.client.get("/v1/exports", headers=auth)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["id"] for i in items] == [str(e3), str(e2), str(e1)]  # новые сверху, чужих нет
    first = items[-1]
    assert first["document_id"] == d1 and first["destination_id"] == str(dest)
    assert first["destination_name"] == "1С бухгалтерия" and first["kind"] == "test_fake"
    assert first["purpose"] == "export" and first["status"] == "done" and first["external_id"] == "42"
    assert first["error"] is None and first["updated_at"]
    assert items[0]["destination_id"] is None and items[0]["destination_name"] is None and items[0]["kind"] is None

    r = await api.client.get("/v1/exports", headers=auth, params={"document_id": d2, "status": "failed"})
    assert [i["id"] for i in r.json()["items"]] == [str(e3), str(e2)]
    r = await api.client.get("/v1/exports", headers=auth, params={"status": "done", "limit": 1})
    assert [i["id"] for i in r.json()["items"]] == [str(e1)]
    assert (await api.client.get("/v1/exports", headers=auth, params={"status": "?"})).status_code == 422
    assert (await api.client.get("/v1/exports", headers=auth, params={"document_id": "x"})).status_code == 422
    assert (await api.client.get("/v1/exports")).status_code == 401


DEST_OFF_TEXT = "получатель отключён"


async def test_summary_counts_failed_and_pending_per_destination(api, auth, make_tenant, fake_connector):
    a = await add_dest(api, auth, name="1С")
    b = await add_dest(api, auth, name="Таблица")
    quiet = await add_dest(api, auth, name="Без выгрузок")
    docs = [await add_doc(api, auth) for _ in range(4)]
    await add_export(api, auth, docs[0], a, status="failed")
    await add_export(api, auth, docs[1], a, status="failed")
    await add_export(api, auth, docs[2], a, status="pending")
    await add_export(api, auth, docs[3], a, status="done")
    await add_export(api, auth, docs[0], b, status="skipped", last_error="уведомление не актуально")
    await add_export(api, auth, docs[1], b, status="pending")
    await add_export(api, auth, docs[2], None, purpose="notify_new", status="failed")  # получатель удалён
    other = await make_tenant("Чужой")
    od = await add_doc(api, other)
    other_dest = await add_dest(api, other, name="Чужой")
    await add_export(api, other, od, other_dest, status="failed")

    r = await api.client.get("/v1/exports/summary", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    by_dest = {i["destination_id"]: i for i in body["items"]}
    assert by_dest[str(a)] == {"destination_id": str(a), "failed": 2, "pending": 1}
    assert by_dest[str(b)] == {"destination_id": str(b), "failed": 0, "pending": 1}  # skipped — не ошибка
    assert str(quiet) not in by_dest and str(other_dest) not in by_dest
    assert body["failed"] == 3  # вместе с удалённым получателем
    operator, _ = await session_for(api, auth, role="operator")
    assert (await api.client.get("/v1/exports/summary", headers=operator)).status_code == 200
    assert (await api.client.get("/v1/exports/summary")).status_code == 401


async def test_list_filters_by_destination_and_skipped_status(api, auth, fake_connector):
    a = await add_dest(api, auth, name="1С")
    b = await add_dest(api, auth, name="Таблица")
    d = await add_doc(api, auth)
    ea = await add_export(api, auth, d, a, status="failed")
    await add_export(api, auth, d, b, status="failed")
    es = await add_export(api, auth, d, b, purpose="notify_new", status="skipped")
    r = await api.client.get("/v1/exports", headers=auth, params={"destination_id": str(a), "status": "failed"})
    assert [i["id"] for i in r.json()["items"]] == [str(ea)]
    r = await api.client.get("/v1/exports", headers=auth, params={"status": "skipped"})
    assert [i["id"] for i in r.json()["items"]] == [str(es)]
    assert (await api.client.post(f"/v1/exports/{es}/retry", headers=auth)).status_code == 409


async def test_document_payload_has_exports(api, auth, fake_connector):
    dest = await add_dest(api, auth)
    doc_id = await add_doc(api, auth)
    assert (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).json()["exports"] == []
    e = await add_export(api, auth, doc_id, dest, status="failed", last_error="нет связи", attempt=6)
    exports = (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).json()["exports"]
    assert len(exports) == 1
    assert set(exports[0]) == {
        "id", "destination_id", "destination_name", "kind", "purpose", "status", "external_id", "error", "updated_at",
    }
    assert exports[0]["id"] == str(e) and exports[0]["error"] == "нет связи"


# --- повторить ------------------------------------------------------------


async def test_retry_failed_export(api, auth, fake_connector):
    dest = await add_dest(api, auth)
    doc_id = await add_doc(api, auth)
    e = await add_export(api, auth, doc_id, dest, status="failed", last_error="нет связи", attempt=6)
    r = await api.client.post(f"/v1/exports/{e}/retry", headers=auth)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "pending" and r.json()["error"] is None
    async with api.sessionmaker() as s:
        row = await s.get(Export, e)
        assert row.status == "pending" and row.attempt == 0 and row.next_attempt_at is not None
    assert export_calls(api) == [(DELIVER_EXPORT, (str(e), 0), None, export_job_id(e, 0))]

    # уже pending — 409, вторая задача не ставится
    r = await api.client.post(f"/v1/exports/{e}/retry", headers=auth)
    assert r.status_code == 409
    assert len(export_calls(api)) == 1


async def test_retry_done_is_conflict(api, auth, fake_connector):
    dest = await add_dest(api, auth)
    doc_id = await add_doc(api, auth)
    e = await add_export(api, auth, doc_id, dest, status="done", external_id="1")
    assert (await api.client.post(f"/v1/exports/{e}/retry", headers=auth)).status_code == 409
    assert export_calls(api) == []


async def test_retry_foreign_or_missing_404(api, auth, make_tenant, fake_connector):
    other = await make_tenant("Чужой")
    dest = await add_dest(api, other)
    od = await add_doc(api, other)
    e = await add_export(api, other, od, dest, status="failed")
    assert (await api.client.post(f"/v1/exports/{e}/retry", headers=auth)).status_code == 404
    assert (await api.client.post(f"/v1/exports/{uuid.uuid4()}/retry", headers=auth)).status_code == 404
    assert (await api.client.post("/v1/exports/x/retry", headers=auth)).status_code == 404


async def test_retry_operator_forbidden_owner_allowed(api, auth, fake_connector):
    dest = await add_dest(api, auth)
    doc_id = await add_doc(api, auth)
    e = await add_export(api, auth, doc_id, dest, status="failed")
    operator, _ = await session_for(api, auth, role="operator")
    assert (await api.client.post(f"/v1/exports/{e}/retry", headers=operator)).status_code == 403
    # журнал оператор читать может
    assert (await api.client.get("/v1/exports", headers=operator)).status_code == 200
    owner, _ = await session_for(api, auth, role="owner")
    assert (await api.client.post(f"/v1/exports/{e}/retry", headers=owner)).status_code == 200


async def test_journal_labels_bot_export(api, auth):
    doc_id = await add_doc(api, auth)
    async with api.sessionmaker() as s:
        bot = TenantBot(
            tenant_id=await tenant_of(api, auth), token_enc="x", token_hash="h", username="acme_bot", invite_code="c"
        )
        s.add(bot)
        await s.flush()
        chat = BotChat(bot_id=bot.id, chat_id=500, title="Чат")
        s.add(chat)
        await s.commit()
    e = await add_export(
        api, auth, doc_id, None, bot_chat_id=chat.id, purpose="bot_ready", status="failed", attempt=1,
        last_error="Telegram sendMessage: 403",
    )

    [item] = (await api.client.get("/v1/exports", headers=auth)).json()["items"]
    assert item["destination_name"] == "Telegram-бот" and item["kind"] == "tenant_bot"
    assert item["destination_id"] is None and item["purpose"] == "bot_ready"
    [in_doc] = (await api.client.get(f"/v1/documents/{doc_id}", headers=auth)).json()["exports"]
    assert in_doc["destination_name"] == "Telegram-бот"
    assert (await api.client.get("/v1/exports/summary", headers=auth)).json() == {"items": [], "failed": 1}

    r = await api.client.post(f"/v1/exports/{e}/retry", headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "pending" and body["attempt"] == 0 and body["error"] is None
    assert body["destination_name"] == "Telegram-бот"
    assert api.enqueuer.calls[-1] == (DELIVER_EXPORT, (str(e), 0), None, export_job_id(e, 0))
