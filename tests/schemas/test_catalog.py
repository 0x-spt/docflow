from docflow.db.models import SchemaRecord, Tenant
from docflow.schemas.catalog import AUTO_SLUG, DocType, list_doc_types, match_doc_type, normalize_title

TYPES = [
    DocType("invoice_ru", 1, "Счёт на оплату", "", True),
    DocType("act_ru", 1, "Акт", "", True),
]


def test_match_ignores_case_and_yo():
    assert match_doc_type("счёт на оплату", TYPES) == "invoice_ru"
    assert match_doc_type("СЧЕТ  НА ОПЛАТУ ", TYPES) == "invoice_ru"


def test_match_by_slug():
    assert match_doc_type("act_ru", TYPES) == "act_ru"
    assert match_doc_type("ACT_RU", TYPES) == "act_ru"


def test_match_none_for_empty():
    assert match_doc_type(None, TYPES) is None
    assert match_doc_type("   ", TYPES) is None
    assert match_doc_type("накладная", TYPES) is None


def test_match_first_by_slug_on_tie():
    twins = [DocType("b", 1, "Договор", "", False), DocType("a", 1, "договор", "", False)]
    assert match_doc_type("Договор", twins) == "a"


def test_match_ignores_trailing_parenthetical():
    types = [DocType("invoice_ru", 1, "Счёт на оплату (РФ)", "", True)]
    assert match_doc_type("счет на оплату", types) == "invoice_ru"
    assert match_doc_type("счёт на оплату (рф)", types) == "invoice_ru"
    assert match_doc_type("(рф)", types) is None
    assert match_doc_type("счет", types) is None


def test_match_exact_title_beats_trimmed():
    types = [DocType("a_ru", 1, "Счёт на оплату (РФ)", "", True), DocType("b_own", 1, "Счёт на оплату", "", False)]
    assert match_doc_type("счет на оплату", types) == "b_own"


def test_normalize_title():
    assert normalize_title("  Счёт   НА\tоплату ") == "счет на оплату"


async def test_tenant_override_title_wins(session):
    tenant = Tenant(name="t", extractor="claude", auto_ok=True)
    session.add(tenant)
    await session.flush()
    fields = [{"name": "total", "type": "number"}]
    body = {"slug": "invoice_ru", "title": "Инвойс клиента", "version": 2, "fields": fields}
    session.add(SchemaRecord(tenant_id=tenant.id, slug="invoice_ru", version=2, body=body))
    await session.commit()

    types = await list_doc_types(session, tenant.id)
    assert match_doc_type("инвойс клиента", types) == "invoice_ru"
    assert match_doc_type("счёт на оплату (рф)", types) is None
    own = next(t for t in types if t.slug == "invoice_ru")
    assert (own.version, own.builtin) == (2, False)
    assert [t.slug for t in types] == sorted(t.slug for t in types)


async def test_auto_never_listed(session):
    tenant = Tenant(name="t", extractor="claude", auto_ok=True)
    session.add(tenant)
    await session.flush()
    body = {"slug": AUTO_SLUG, "title": "Авто", "version": 1, "fields": [{"name": "total", "type": "number"}]}
    session.add(SchemaRecord(tenant_id=tenant.id, slug=AUTO_SLUG, version=1, body=body))
    await session.commit()
    assert AUTO_SLUG not in [t.slug for t in await list_doc_types(session, tenant.id)]
