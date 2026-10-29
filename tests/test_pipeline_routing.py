"""Конвейер и маршрутизация: ok → выгрузки, согласование → needs_review, needs_review → уведомления."""

import uuid

from sqlalchemy import select
from test_pipeline import INVOICE, Raising, load, make_deps, make_doc, webhook_calls

from docflow.db.models import Destination, Dictionary, Document, Export, RoutingRules
from docflow.extraction.base import ExtractionError
from docflow.pipeline import process_document
from docflow.queue import DELIVER_EXPORT


async def configure(deps, doc_id, rules, *, notifier=False, dictionaries=None):
    """Два получателя тенанта документа и правила; в правилах «EXP»/«NTF» заменяются на их id."""
    async with deps.sessionmaker() as s:
        tenant_id = (await s.get(Document, doc_id)).tenant_id
        exp = Destination(tenant_id=tenant_id, kind="test_fake", name="Система", config={}, mapping={})
        ntf = Destination(
            tenant_id=tenant_id, kind="test_notifier" if notifier else "test_fake", name="Оповещения",
            config={}, mapping={},
        )
        s.add_all([exp, ntf])
        for name, values in (dictionaries or {}).items():
            s.add(Dictionary(tenant_id=tenant_id, name=name, title=name,
                             entries=[{"value": v, "aliases": []} for v in values]))
        await s.flush()
        ids = {"EXP": str(exp.id), "NTF": str(ntf.id)}
        stored = [
            {
                "name": r.get("name", "правило"),
                "schema": r.get("schema"),
                "conditions": r.get("conditions", []),
                "export_to": [ids[x] for x in r.get("export_to", [])],
                "notify": [ids[x] for x in r.get("notify", [])],
                "require_approval": r.get("require_approval", False),
            }
            for r in rules
        ]
        s.add(RoutingRules(tenant_id=tenant_id, rules=stored))
        await s.commit()
        return exp.id, ntf.id


async def exports(deps, doc_id):
    async with deps.sessionmaker() as s:
        rows = (await s.execute(select(Export).where(Export.document_id == doc_id))).scalars().all()
        return sorted((r.destination_id, r.purpose, r.status) for r in rows)


def export_calls(deps):
    return [c for c in deps.enqueuer.calls if c[0] == DELIVER_EXPORT]


async def test_ok_document_is_exported_and_announced(sm, tmp_path, fake_connector):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    exp, ntf = await configure(deps, doc_id, [{"export_to": ["EXP"], "notify": ["NTF"]}])
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    assert doc.status == "ok", doc.issues
    assert await exports(deps, doc_id) == sorted([(exp, "export", "pending"), (ntf, "notify_new", "pending")])
    assert len(export_calls(deps)) == 2
    assert len(webhook_calls(deps)) == 1


async def test_no_rules_nothing_exported(sm, tmp_path, fake_connector):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    await process_document(deps, doc_id)
    assert (await load(deps, doc_id)).status == "ok"
    assert await exports(deps, doc_id) == [] and export_calls(deps) == []


async def test_rule_conditions_and_schema_filter(sm, tmp_path, fake_connector):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    exp, _ = await configure(
        deps,
        doc_id,
        [
            {"conditions": [{"field": "total", "op": "gt", "value": 1000}], "export_to": ["NTF"]},
            {"schema": "upd_ru", "export_to": ["NTF"]},
            {"schema": "invoice_ru", "conditions": [{"field": "total", "op": "le", "value": "250"}],
             "export_to": ["EXP"]},
        ],
    )
    await process_document(deps, doc_id)
    assert await exports(deps, doc_id) == [(exp, "export", "pending")]


async def test_in_dictionary_rule_uses_tenant_dictionary(sm, tmp_path, fake_connector):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    exp, _ = await configure(
        deps,
        doc_id,
        [{"conditions": [{"field": "supplier", "op": "in_dictionary", "value": "Ключевые"}], "export_to": ["EXP"]}],
        dictionaries={"Ключевые": ["ООО «Ромашка»"]},
    )
    await process_document(deps, doc_id)
    assert await exports(deps, doc_id) == [(exp, "export", "pending")]


async def test_approval_rule_turns_ok_into_needs_review(sm, tmp_path, fake_connector):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    exp, ntf = await configure(
        deps,
        doc_id,
        [
            {"name": "Крупные суммы", "conditions": [{"field": "total", "op": "ge", "value": 100}],
             "require_approval": True, "notify": ["NTF"]},
            {"name": "Всё в систему", "export_to": ["EXP"]},
            {"name": "Не подходит", "conditions": [{"field": "total", "op": "lt", "value": 1}],
             "require_approval": True},
        ],
    )
    await process_document(deps, doc_id)

    doc = await load(deps, doc_id)
    # статус записан до постановки вебхука: тело document.completed покажет needs_review
    assert doc.status == "needs_review"
    assert doc.issues == [{"field": None, "code": "approval_required", "message": "нужно согласование: Крупные суммы"}]
    # в учётную систему — ничего; оператору — уведомление «на проверку»
    assert await exports(deps, doc_id) == [(ntf, "notify_review", "pending")]
    assert len(webhook_calls(deps)) == 1


async def test_needs_review_from_checks_only_notifies(sm, tmp_path, fake_connector):
    deps = make_deps(sm, tmp_path, responses={"invoice_ru": {**INVOICE, "subtotal": 300}})
    doc_id = await make_doc(deps)
    _, ntf = await configure(deps, doc_id, [{"export_to": ["EXP"], "notify": ["NTF"], "require_approval": True}])
    await process_document(deps, doc_id)
    doc = await load(deps, doc_id)
    assert doc.status == "needs_review"
    # согласование не добавляется к документу, который и так идёт человеку
    assert "approval_required" not in [i["code"] for i in doc.issues]
    assert await exports(deps, doc_id) == [(ntf, "notify_review", "pending")]


async def test_untrusted_tenant_needs_review_notifies(sm, tmp_path, fake_connector, fake_notifier):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps, auto_ok=False)
    _, ntf = await configure(deps, doc_id, [{"export_to": ["EXP"], "notify": ["NTF"]}], notifier=True)
    await process_document(deps, doc_id)
    assert (await load(deps, doc_id)).status == "needs_review"
    assert await exports(deps, doc_id) == [(ntf, "notify_review", "pending")]


async def test_failed_document_routes_nothing(sm, tmp_path, fake_connector):
    deps = make_deps(sm, tmp_path, extractor=Raising(ExtractionError("отказ")))
    doc_id = await make_doc(deps)
    await configure(deps, doc_id, [{"export_to": ["EXP"], "notify": ["NTF"]}])
    await process_document(deps, doc_id)
    assert (await load(deps, doc_id)).status == "failed"
    assert await exports(deps, doc_id) == []


async def test_broken_rules_do_not_break_pipeline(sm, tmp_path, fake_connector):
    deps = make_deps(sm, tmp_path)
    doc_id = await make_doc(deps)
    async with deps.sessionmaker() as s:
        tenant_id = (await s.get(Document, doc_id)).tenant_id
        s.add(RoutingRules(tenant_id=tenant_id, rules=[{"name": "битое", "conditions": "?"},
                                                       {"name": "мёртвый", "export_to": [str(uuid.uuid4())]}]))
        await s.commit()
    await process_document(deps, doc_id)
    assert (await load(deps, doc_id)).status == "ok"
    assert await exports(deps, doc_id) == []


async def test_routing_skipped_if_status_changed_before_routing(sm, tmp_path, fake_connector, monkeypatch):
    """Оператор одобрил документ между записью результата и постановкой выгрузок: «на проверку» уже не шлём,
    а выгрузки ставит путь одобрения сам."""
    from docflow import pipeline

    deps = make_deps(sm, tmp_path, responses={"invoice_ru": {**INVOICE, "subtotal": 300}})
    doc_id = await make_doc(deps)
    await configure(deps, doc_id, [{"export_to": ["EXP"], "notify": ["NTF"]}])
    original = pipeline._enqueue_webhooks

    async def approve_meanwhile(session, document_id, enqueuer):
        await original(session, document_id, enqueuer)
        async with deps.sessionmaker() as s:
            (await s.get(Document, document_id)).status = "ok"
            await s.commit()

    monkeypatch.setattr(pipeline, "_enqueue_webhooks", approve_meanwhile)
    await process_document(deps, doc_id)
    assert (await load(deps, doc_id)).status == "ok"
    assert await exports(deps, doc_id) == [] and export_calls(deps) == []
