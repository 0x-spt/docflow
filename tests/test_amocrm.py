"""amoCRM: проверка токена, сделка с компанией одним запросом, поиск без дублей, ошибки доступа и лимитов."""

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from docflow.connectors.base import REGISTRY, ConnectorError, DestCtx, ExportDoc
from docflow.schemas.library import builtin_schemas

API = "https://company.amocrm.ru/api/v4"
DOC_ID = "11111111-1111-1111-1111-111111111111"
TAG = "docflow111111111111"


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make_ctx(http, mapping=None, secrets=None, **config) -> DestCtx:
    config.setdefault("subdomain", "company")
    return DestCtx(
        config=config,
        secrets={"access_token": "t0k3n"} if secrets is None else secrets,
        mapping=mapping or {},
        http=http,
    )


def make_doc(**data) -> ExportDoc:
    schema = builtin_schemas()["invoice_ru"]
    return ExportDoc(
        id=uuid.UUID(DOC_ID),
        tenant_id=uuid.uuid4(),
        schema=schema,
        data={
            "number": "42",
            "issued": "2026-10-05",
            "supplier": "ООО Ромашка",
            "supplier_inn": "7707083893",
            "buyer": "ООО Лютик",
            "buyer_inn": "5003052454",
            "total": 1250.5,
            **data,
        },
        status="ok",
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        title=schema.title,
        number="42",
        issued=datetime(2026, 10, 5).date(),
        cabinet_url="https://site/app/documents/1",
        digital_pdf_url="https://api/v1/documents/1/digital.pdf",
        scan_pdf_url="https://api/v1/documents/1/pdf",
    )


class Amo:
    """Имитация amoCRM v4: лиды с примечаниями, компании; пустой поиск отвечает 204 без тела."""

    def __init__(self, router, companies=None, leads=None):
        self.companies: list[dict] = list(companies or [])
        self.leads: dict[int, dict] = dict(leads or {})
        self.notes: dict[int, list[str]] = {}
        self.calls: list[tuple[str, str, object]] = []
        self.fail: dict[tuple[str, str], httpx.Response] = {}
        self.next_id = 500
        router.route(url__startswith=API).mock(side_effect=self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path.removeprefix("/api/v4")
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, path, body))
        assert request.headers["Authorization"] == "Bearer t0k3n"
        if (request.method, path) in self.fail:
            return self.fail[(request.method, path)]
        query = request.url.params.get("query")
        if request.method == "GET" and path == "/account":
            return httpx.Response(200, json={"id": 1, "name": "Моя компания"})
        if request.method == "GET" and path == "/leads":
            found = [
                {"id": i, "name": lead["name"]} for i, lead in self.leads.items() if query and query in lead["name"]
            ]
            return self.listing("leads", found)
        if request.method == "GET" and path == "/companies":
            found = [
                c
                for c in self.companies
                if query and (query in c["name"] or query in json.dumps(c, ensure_ascii=False))
            ]
            return self.listing("companies", found)
        if request.method == "POST" and path == "/leads/complex":
            item = body[0]
            self.next_id += 1
            self.leads[self.next_id] = item
            return httpx.Response(
                200, json=[{"id": self.next_id, "contact_id": None, "company_id": 9, "request_id": "0"}]
            )
        if path.startswith("/leads/") and path.endswith("/notes"):
            lead_id = int(path.split("/")[2])
            if request.method == "GET":
                found = [{"id": 1, "params": {"text": t}} for t in self.notes.get(lead_id, [])]
                return self.listing("notes", found)
            self.notes.setdefault(lead_id, []).append(body[0]["params"]["text"])
            return httpx.Response(200, json={"_embedded": {"notes": [{"id": 1}]}})
        return httpx.Response(404)

    @staticmethod
    def listing(key, items):
        if not items:
            return httpx.Response(204)
        return httpx.Response(200, json={"_embedded": {key: items}})

    def posts(self, path):
        return [b for m, p, b in self.calls if m == "POST" and p == path]


async def test_registered_and_fields():
    c = REGISTRY["amocrm"]
    assert c.secret_fields[0].name == "access_token"
    assert {f.name for f in c.config_fields} == {
        "subdomain",
        "pipeline_id",
        "status_id",
        "responsible_user_id",
        "inn_field_id",
        "counterparty",
    }


async def test_test_account(http):
    with respx.mock() as router:
        Amo(router)
        assert await REGISTRY["amocrm"].test(make_ctx(http)) == "Аккаунт «Моя компания» доступен"


@pytest.mark.parametrize(
    "sub", ["company", "Company", "company.amocrm.ru", "https://company.amocrm.ru/", " company.amocrm.ru/api "]
)
async def test_subdomain_normalised(http, sub):
    with respx.mock() as router:
        Amo(router)
        assert await REGISTRY["amocrm"].test(make_ctx(http, subdomain=sub))


async def test_kommo_domain(http):
    with respx.mock() as router:
        router.get("https://acme.kommo.com/api/v4/account").respond(json={"name": "Acme"})
        assert "Acme" in await REGISTRY["amocrm"].test(make_ctx(http, subdomain="acme.kommo.com"))


@pytest.mark.parametrize("sub", ["", "evil.com", "a.b.amocrm.ru", "company.amocrm.ru.evil.com", "a b", "x/y"])
async def test_bad_subdomain(http, sub):
    with pytest.raises(ConnectorError) as err:
        await REGISTRY["amocrm"].test(make_ctx(http, subdomain=sub))
    assert not err.value.retryable


async def test_export_creates_lead_with_new_company(http):
    with respx.mock() as router:
        a = Amo(router)
        ext = await REGISTRY["amocrm"].export(
            make_ctx(http, pipeline_id=11, status_id=22, responsible_user_id=33, inn_field_id=44), make_doc()
        )
    lead = a.posts("/leads/complex")[0][0]
    assert lead["name"] == f"Счёт на оплату (РФ) № 42 от 05.10.2026 {TAG}"
    assert lead["price"] == 1251  # amoCRM хранит бюджет целым числом
    assert (lead["pipeline_id"], lead["status_id"], lead["responsible_user_id"]) == (11, 22, 33)
    company = lead["_embedded"]["companies"][0]
    assert company["name"] == "ООО Ромашка"
    assert company["custom_fields_values"] == [{"field_id": 44, "values": [{"value": "7707083893"}]}]
    assert lead["_embedded"]["tags"] == [{"name": "DocFlow"}]
    assert ext == str(a.next_id)
    text = a.notes[a.next_id][0]
    assert "digital.pdf" in text and f"docflow:{DOC_ID}" in text


async def test_company_found_by_inn_field(http):
    existing = {
        "id": 77,
        "name": "Ромашка (старое)",
        "custom_fields_values": [{"field_id": 44, "values": [{"value": "7707083893"}]}],
    }
    with respx.mock() as router:
        a = Amo(router, companies=[existing])
        await REGISTRY["amocrm"].export(make_ctx(http, inn_field_id=44), make_doc())
    assert a.posts("/leads/complex")[0][0]["_embedded"]["companies"] == [{"id": 77}]


async def test_inn_field_value_must_match(http):
    other = {"id": 78, "name": "Чужая", "custom_fields_values": [{"field_id": 44, "values": [{"value": "9999999999"}]}]}
    with respx.mock() as router:
        a = Amo(router, companies=[other])
        await REGISTRY["amocrm"].export(make_ctx(http, inn_field_id=44), make_doc())
    assert a.posts("/leads/complex")[0][0]["_embedded"]["companies"][0]["name"] == "ООО Ромашка"


async def test_company_found_by_exact_name(http):
    with respx.mock() as router:
        a = Amo(router, companies=[{"id": 80, "name": "ООО Ромашка плюс"}, {"id": 79, "name": "ООО Ромашка"}])
        await REGISTRY["amocrm"].export(make_ctx(http), make_doc(supplier_inn=None))
    assert a.posts("/leads/complex")[0][0]["_embedded"]["companies"] == [{"id": 79}]


async def test_without_inn_field_company_has_no_custom_fields(http):
    with respx.mock() as router:
        a = Amo(router)
        await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert "custom_fields_values" not in a.posts("/leads/complex")[0][0]["_embedded"]["companies"][0]


async def test_no_counterparty_no_company_no_price(http):
    with respx.mock() as router:
        a = Amo(router)
        await REGISTRY["amocrm"].export(make_ctx(http), make_doc(supplier=None, supplier_inn=None, total=None))
    lead = a.posts("/leads/complex")[0][0]
    assert "companies" not in lead["_embedded"] and "price" not in lead
    assert not [c for c in a.calls if c[1] == "/companies"]


async def test_counterparty_buyer(http):
    with respx.mock() as router:
        a = Amo(router)
        await REGISTRY["amocrm"].export(make_ctx(http, counterparty="buyer"), make_doc())
    assert a.posts("/leads/complex")[0][0]["_embedded"]["companies"][0]["name"] == "ООО Лютик"


async def test_repeat_does_not_duplicate(http):
    with respx.mock() as router:
        a = Amo(router)
        first = await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
        a.calls.clear()
        second = await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert first == second and len(a.leads) == 1
    assert not a.posts("/leads/complex") and not a.posts(f"/leads/{first}/notes")
    assert len(a.notes[int(first)]) == 1


async def test_repeat_after_failed_note_adds_note_only(http):
    with respx.mock() as router:
        a = Amo(router)
        a.fail[("POST", "/leads/501/notes")] = httpx.Response(503)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
        assert err.value.retryable
        del a.fail[("POST", "/leads/501/notes")]
        ext = await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert len(a.leads) == 1 and ext == "501" and len(a.notes[501]) == 1


async def test_lead_with_other_doc_tag_is_not_a_match(http):
    with respx.mock() as router:
        a = Amo(router, leads={1: {"name": "x docflow222222222222"}})
        await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert len(a.leads) == 2


async def test_tag_must_be_at_end_of_name(http):
    with respx.mock() as router:
        a = Amo(router, leads={1: {"name": f"подделка {TAG} хвост"}})
        await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert len(a.leads) == 2


async def test_long_title_truncated_keeps_tag(http):
    with respx.mock() as router:
        a = Amo(router)
        doc = make_doc()
        doc.title = "Я" * 400
        await REGISTRY["amocrm"].export(make_ctx(http), doc)
    name = a.posts("/leads/complex")[0][0]["name"]
    assert len(name) <= 255 and name.endswith(" " + TAG)


async def test_native_value_types(http):
    # даты — Unix-время полуночи UTC
    with respx.mock() as router:
        a = Amo(router)
        await REGISTRY["amocrm"].export(make_ctx(http, mapping={"issued": "556"}), make_doc())
    cf = a.posts("/leads/complex")[0][0]["custom_fields_values"]
    assert cf == [{"field_id": 556, "values": [{"value": 1791158400}]}]


def test_boolean_native():
    from docflow.connectors.crm_common import field_value
    from docflow.schemas.model import ColumnSpec

    spec = ColumnSpec(name="paid", type="boolean")
    assert field_value(spec, True, native=True) is True
    assert field_value(spec, False, native=True) is False
    assert field_value(spec, True) == "да"


@pytest.mark.parametrize("code,retryable", [(302, False), (408, True)])
async def test_redirect_and_timeout_codes(http, code, retryable):
    with respx.mock() as router:
        Amo(router).fail[("GET", "/leads")] = httpx.Response(code)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert err.value.retryable is retryable
    if not retryable:
        assert err.value.message == "неверный адрес аккаунта amoCRM (перенаправление)"


async def test_mapped_fields(http):
    with respx.mock() as router:
        a = Amo(router)
        mapping = {"number": "555", "issued": "556", "vat": "557"}
        await REGISTRY["amocrm"].export(make_ctx(http, mapping=mapping), make_doc(vat=250))
    cf = a.posts("/leads/complex")[0][0]["custom_fields_values"]
    assert {"field_id": 555, "values": [{"value": "42"}]} in cf
    assert {"field_id": 556, "values": [{"value": 1791158400}]} in cf
    assert {"field_id": 557, "values": [{"value": 250}]} in cf


async def test_mapping_non_numeric_rejected(http):
    with respx.mock(assert_all_called=False) as router:
        a = Amo(router)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["amocrm"].export(make_ctx(http, mapping={"number": "UF_X"}), make_doc())
    assert not err.value.retryable and not a.posts("/leads/complex")


async def test_control_chars_stripped(http):
    with respx.mock() as router:
        a = Amo(router)
        await REGISTRY["amocrm"].export(make_ctx(http), make_doc(supplier="ООО\x00 Ро\x07машка"))
    assert a.posts("/leads/complex")[0][0]["_embedded"]["companies"][0]["name"] == "ООО Ромашка"


async def test_401_not_retryable(http):
    with respx.mock() as router:
        Amo(router).fail[("GET", "/leads")] = httpx.Response(401, json={"title": "Unauthorized"})
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert not err.value.retryable
    assert err.value.message == "токен amoCRM не принят — выпустите новый долгосрочный токен"
    assert "t0k3n" not in err.value.message


async def test_402_not_retryable(http):
    with respx.mock() as router:
        Amo(router).fail[("GET", "/leads")] = httpx.Response(402)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert not err.value.retryable and "не оплачен" in err.value.message


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_transient_retryable(http, code):
    with respx.mock() as router:
        Amo(router).fail[("POST", "/leads/complex")] = httpx.Response(code)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert err.value.retryable


async def test_timeout_retryable(http):
    with respx.mock() as router:
        router.route(url__startswith=API).mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["amocrm"].test(make_ctx(http))
    assert err.value.retryable


async def test_validation_400_message(http):
    with respx.mock() as router:
        Amo(router).fail[("POST", "/leads/complex")] = httpx.Response(
            400, json={"title": "Bad Request", "detail": "Статус не найден", "status": 400}
        )
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["amocrm"].export(make_ctx(http), make_doc())
    assert not err.value.retryable and "Статус не найден" in err.value.message


async def test_missing_token(http):
    with pytest.raises(ConnectorError) as err:
        await REGISTRY["amocrm"].test(make_ctx(http, secrets={}))
    assert not err.value.retryable
