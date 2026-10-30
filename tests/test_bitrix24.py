"""Битрикс24: проверка вебхука, компания по ИНН/названию, сделка без дублей, ошибки доступа и лимитов."""

import json
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from docflow.connectors.base import REGISTRY, ConnectorError, DestCtx, ExportDoc, RemoteField
from docflow.schemas.library import builtin_schemas

HOOK = "https://portal.bitrix24.ru/rest/1/s3cr3tk3y/"
DOC_ID = "11111111-1111-1111-1111-111111111111"


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make_ctx(http, mapping=None, secrets=None, **config) -> DestCtx:
    return DestCtx(
        config=config,
        secrets={"webhook_url": HOOK} if secrets is None else secrets,
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
            "supplier_kpp": "770701001",
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
        digital_pdf_url="https://site/app/files/1/digital",
        scan_pdf_url="https://site/app/files/1/pdf",
    )


class Bitrix:
    """Минимальная имитация REST Битрикс24: хранит компании, реквизиты, сделки, комментарии."""

    def __init__(self, router, companies=None, requisites=None, deals=None):
        self.companies = dict(companies or {})  # id -> title
        self.requisites = list(requisites or [])  # {ENTITY_ID, RQ_INN}
        self.deals: dict[str, dict] = dict(deals or {})
        self.comments: list[dict] = []
        self.calls: list[tuple[str, dict]] = []
        self.raw_filters: list[dict] = []
        self.next_id = 100
        self.fail: dict[str, httpx.Response] = {}
        router.post(url__startswith=HOOK).mock(side_effect=self.handle)

    def new_id(self):
        self.next_id += 1
        return self.next_id

    def handle(self, request: httpx.Request) -> httpx.Response:
        method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
        body = json.loads(request.content or b"{}")
        self.calls.append((method, body))
        if method in self.fail:
            return self.fail[method]
        flt = {k.lstrip("="): v for k, v in body.get("filter", {}).items()}
        self.raw_filters.append(body.get("filter", {}))
        result: object
        if method == "profile":
            result = {"NAME": "Иван", "LAST_NAME": "Петров"}
        elif method == "crm.deal.list":
            result = [
                {"ID": i}
                for i, d in self.deals.items()
                if d.get("ORIGINATOR_ID") == flt.get("ORIGINATOR_ID") and d.get("ORIGIN_ID") == flt.get("ORIGIN_ID")
            ]
        elif method == "crm.requisite.list":
            result = [
                {"ENTITY_ID": r["ENTITY_ID"], "RQ_INN": r["RQ_INN"]}
                for r in self.requisites
                if ("RQ_INN" not in flt or r["RQ_INN"] == flt["RQ_INN"])
                and ("ENTITY_ID" not in flt or str(r["ENTITY_ID"]) == str(flt["ENTITY_ID"]))
            ]
        elif method == "crm.company.list":
            result = [{"ID": i} for i, t in self.companies.items() if t == flt.get("TITLE")]
        elif method == "crm.company.add":
            cid = str(self.new_id())
            self.companies[cid] = body["fields"]["TITLE"]
            result = int(cid)
        elif method == "crm.requisite.add":
            self.requisites.append({"ENTITY_ID": str(body["fields"]["ENTITY_ID"]), "RQ_INN": body["fields"]["RQ_INN"]})
            result = self.new_id()
        elif method == "crm.deal.add":
            did = str(self.new_id())
            self.deals[did] = body["fields"]
            result = int(did)
        elif method == "crm.timeline.comment.list":
            result = [c for c in self.comments if str(c["ENTITY_ID"]) == str(flt.get("ENTITY_ID"))]
        elif method == "crm.timeline.comment.add":
            self.comments.append(body["fields"])
            result = self.new_id()
        else:
            return httpx.Response(400, json={"error": "ERROR_METHOD_NOT_FOUND", "error_description": method})
        return httpx.Response(200, json={"result": result})

    def methods(self):
        return [m for m, _ in self.calls]

    def bodies(self, method):
        return [b for m, b in self.calls if m == method]


async def test_registered_and_fields():
    c = REGISTRY["bitrix24"]
    assert c.secret_fields[0].name == "webhook_url" and c.secret_fields[0].type == "url"
    assert {f.name for f in c.config_fields} == {"assigned_by_id", "category_id", "stage_id", "counterparty"}


async def test_test_profile(http):
    with respx.mock() as router:
        Bitrix(router)
        msg = await REGISTRY["bitrix24"].test(make_ctx(http))
    assert msg == "Портал доступен, пользователь Иван Петров"


async def test_export_creates_company_requisite_deal_comment(http):
    with respx.mock() as router:
        b = Bitrix(router)
        ext = await REGISTRY["bitrix24"].export(
            make_ctx(http, assigned_by_id=7, category_id=2, stage_id="C2:NEW"), make_doc()
        )
    assert b.methods() == [
        "crm.deal.list",
        "crm.requisite.list",
        "crm.company.list",
        "crm.company.add",
        "crm.requisite.add",
        "crm.deal.add",
        "crm.timeline.comment.list",
        "crm.timeline.comment.add",
    ]
    req = b.bodies("crm.requisite.add")[0]["fields"]
    assert req["RQ_INN"] == "7707083893" and req["RQ_KPP"] == "770701001" and req["ENTITY_TYPE_ID"] == 4
    assert req["PRESET_ID"] == 1
    fields = b.bodies("crm.deal.add")[0]["fields"]
    assert fields["TITLE"] == "Счёт на оплату (РФ) № 42 от 05.10.2026"
    assert fields["OPPORTUNITY"] == 1250.5 and fields["CURRENCY_ID"] == "RUB"
    assert fields["ASSIGNED_BY_ID"] == 7 and fields["CATEGORY_ID"] == 2 and fields["STAGE_ID"] == "C2:NEW"
    assert fields["ORIGINATOR_ID"] == "docflow" and fields["ORIGIN_ID"] == DOC_ID
    assert str(fields["COMPANY_ID"]) == "101"
    comment = b.comments[0]
    assert comment["ENTITY_TYPE"] == "deal" and str(comment["ENTITY_ID"]) == ext
    assert "https://site/app/files/1/digital" in comment["COMMENT"]
    assert "после входа в кабинет" in comment["COMMENT"]
    assert f"docflow:{DOC_ID}" in comment["COMMENT"]


async def test_company_found_by_inn(http):
    with respx.mock() as router:
        b = Bitrix(
            router, companies={"55": "Другое название"}, requisites=[{"ENTITY_ID": "55", "RQ_INN": "7707083893"}]
        )
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    assert "crm.company.add" not in b.methods() and "crm.company.list" not in b.methods()
    assert str(b.bodies("crm.deal.add")[0]["fields"]["COMPANY_ID"]) == "55"


async def test_company_found_by_name_when_no_inn(http):
    with respx.mock() as router:
        b = Bitrix(router, companies={"56": "ООО Ромашка"})
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc(supplier_inn=None, supplier_kpp=None))
    assert "crm.requisite.list" not in b.methods()
    assert "crm.company.add" not in b.methods()
    assert str(b.bodies("crm.deal.add")[0]["fields"]["COMPANY_ID"]) == "56"


async def test_company_found_by_name_gets_missing_requisite(http):
    with respx.mock() as router:
        b = Bitrix(router, companies={"56": "ООО Ромашка"})
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
        assert [r["RQ_INN"] for r in b.requisites] == ["7707083893"]
        b.calls.clear()
        await REGISTRY["bitrix24"].export(make_ctx(http, assigned_by_id=1), make_doc(number="43"))
    assert len(b.requisites) == 1  # реквизит уже есть — второй раз не добавляем


async def test_explicit_equality_filters(http):
    with respx.mock() as router:
        b = Bitrix(router)
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    keys = {k for f in b.raw_filters for k in f}
    assert {"=TITLE", "=RQ_INN", "=ORIGIN_ID", "=ORIGINATOR_ID"} <= keys
    assert not keys & {"TITLE", "RQ_INN", "ORIGIN_ID", "ORIGINATOR_ID"}


async def test_method_not_found_message(http):
    with respx.mock() as router:
        Bitrix(router).fail["crm.deal.list"] = httpx.Response(
            404, json={"error": "ERROR_METHOD_NOT_FOUND", "error_description": "Method not found!"}
        )
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    assert not err.value.retryable and err.value.message == "метод не найден — проверьте права вебхука"


async def test_company_without_inn_created_without_requisite(http):
    with respx.mock() as router:
        b = Bitrix(router)
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc(supplier_inn=None))
    assert "crm.company.add" in b.methods() and "crm.requisite.add" not in b.methods()


async def test_ip_inn_uses_entrepreneur_preset(http):
    with respx.mock() as router:
        b = Bitrix(router)
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc(supplier_inn="500100732259", supplier_kpp=None))
    req = b.bodies("crm.requisite.add")[0]["fields"]
    assert req["PRESET_ID"] == 2 and "RQ_KPP" not in req


async def test_counterparty_buyer(http):
    with respx.mock() as router:
        b = Bitrix(router)
        await REGISTRY["bitrix24"].export(make_ctx(http, counterparty="buyer"), make_doc())
    assert b.bodies("crm.company.add")[0]["fields"]["TITLE"] == "ООО Лютик"
    assert b.bodies("crm.requisite.add")[0]["fields"]["RQ_INN"] == "5003052454"


async def test_no_counterparty_no_company(http):
    with respx.mock() as router:
        b = Bitrix(router)
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc(supplier=None, supplier_inn=None, supplier_kpp=None))
    assert not [m for m in b.methods() if "company" in m.split(".") or "requisite" in m]
    assert "COMPANY_ID" not in b.bodies("crm.deal.add")[0]["fields"]


async def test_no_total_no_opportunity(http):
    with respx.mock() as router:
        b = Bitrix(router)
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc(total=None))
    fields = b.bodies("crm.deal.add")[0]["fields"]
    assert "OPPORTUNITY" not in fields and "ASSIGNED_BY_ID" not in fields and "CATEGORY_ID" not in fields


async def test_repeat_does_not_duplicate(http):
    with respx.mock() as router:
        b = Bitrix(router)
        first = await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
        b.calls.clear()
        second = await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    assert first == second
    assert len(b.deals) == 1 and len(b.comments) == 1
    assert "crm.deal.add" not in b.methods() and "crm.timeline.comment.add" not in b.methods()


async def test_repeat_after_failed_comment_adds_comment_only(http):
    with respx.mock() as router:
        b = Bitrix(router)
        b.fail["crm.timeline.comment.add"] = httpx.Response(503, text="")
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
        assert err.value.retryable
        del b.fail["crm.timeline.comment.add"]
        ext = await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    assert len(b.deals) == 1 and len(b.comments) == 1 and ext in b.deals


async def test_mapped_user_fields(http):
    with respx.mock() as router:
        b = Bitrix(router)
        mapping = {"supplier_kpp": "UF_CRM_KPP", "issued": "UF_CRM_DATE", "vat": "UF_CRM_VAT", "number": "UF_CRM_NUM"}
        await REGISTRY["bitrix24"].export(make_ctx(http, mapping=mapping), make_doc(vat=250))
    f = b.bodies("crm.deal.add")[0]["fields"]
    assert f["UF_CRM_KPP"] == "770701001" and f["UF_CRM_DATE"] == "2026-10-05"
    assert f["UF_CRM_VAT"] == 250 and f["UF_CRM_NUM"] == "42"


async def test_mapping_to_system_field_rejected(http):
    with respx.mock(assert_all_called=False) as router:
        b = Bitrix(router)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["bitrix24"].export(make_ctx(http, mapping={"number": "ORIGIN_ID"}), make_doc())
    assert not err.value.retryable and "UF_" in err.value.message
    assert "crm.deal.add" not in b.methods()


async def test_control_chars_stripped(http):
    with respx.mock() as router:
        b = Bitrix(router)
        await REGISTRY["bitrix24"].export(make_ctx(http), make_doc(supplier="ООО\x00 Ро\x07машка"))
    assert b.bodies("crm.company.add")[0]["fields"]["TITLE"] == "ООО Ромашка"


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(
            401, json={"error": "expired_token", "error_description": "The access token provided has expired."}
        ),
        httpx.Response(401, json={"error": "NO_AUTH_FOUND", "error_description": "Wrong authorization data"}),
        httpx.Response(
            403, json={"error": "INSUFFICIENT_SCOPE", "error_description": "The request requires higher privileges"}
        ),
    ],
)
async def test_auth_errors_not_retryable(http, resp):
    with respx.mock() as router:
        Bitrix(router).fail["crm.deal.list"] = resp
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    assert not err.value.retryable
    assert err.value.message.startswith("вебхук Битрикс24 не принят")
    assert "s3cr3tk3y" not in err.value.message


@pytest.mark.parametrize(
    "resp",
    [
        httpx.Response(503, text="unavailable"),
        httpx.Response(429, text=""),
        httpx.Response(503, json={"error": "QUERY_LIMIT_EXCEEDED", "error_description": "Too many requests"}),
        httpx.Response(200, json={"error": "QUERY_LIMIT_EXCEEDED", "error_description": "Too many requests"}),
    ],
)
async def test_transient_errors_retryable(http, resp):
    with respx.mock() as router:
        Bitrix(router).fail["crm.deal.list"] = resp
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    assert err.value.retryable


async def test_timeout_retryable_without_secret(http):
    with respx.mock() as router:
        router.post(url__startswith=HOOK).mock(side_effect=httpx.ConnectTimeout("boom " + HOOK))
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    assert err.value.retryable and "s3cr3tk3y" not in err.value.message


async def test_validation_error_not_retryable(http):
    with respx.mock() as router:
        Bitrix(router).fail["crm.deal.add"] = httpx.Response(
            400, json={"error": "", "error_description": "Поле «Стадия» не найдено"}
        )
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["bitrix24"].export(make_ctx(http), make_doc())
    assert not err.value.retryable and "Стадия" in err.value.message


async def test_missing_webhook_and_bad_numbers(http):
    with pytest.raises(ConnectorError):
        await REGISTRY["bitrix24"].test(make_ctx(http, secrets={}))
    with respx.mock(assert_all_called=False) as router:
        Bitrix(router)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["bitrix24"].export(make_ctx(http, assigned_by_id="abc"), make_doc())
    assert not err.value.retryable


async def test_bitrix_remote_fields_user_fields_only(http):
    with respx.mock() as router:
        route = router.post(f"{HOOK}crm.deal.fields.json").respond(
            json={
                "result": {
                    "TITLE": {"type": "string", "title": "Название"},
                    "UF_CRM_INN": {"type": "string", "title": "UF_CRM_INN", "listLabel": "ИНН", "formLabel": "ИНН"},
                }
            }
        )
        fields = await REGISTRY["bitrix24"].remote_fields(make_ctx(http))
    assert fields == [RemoteField("UF_CRM_INN", "ИНН")]
    assert route.call_count == 1


async def test_bitrix_remote_fields_label_fallbacks(http):
    with respx.mock() as router:
        router.post(f"{HOOK}crm.deal.fields.json").respond(
            json={
                "result": {
                    "UF_CRM_A": {"listLabel": "", "formLabel": "Форма\x07"},
                    "UF_CRM_B": {"title": "Заголовок"},
                    "UF_CRM_C": {},
                }
            }
        )
        fields = await REGISTRY["bitrix24"].remote_fields(make_ctx(http))
    assert fields == [
        RemoteField("UF_CRM_A", "Форма"),
        RemoteField("UF_CRM_B", "Заголовок"),
        RemoteField("UF_CRM_C", "UF_CRM_C"),
    ]


async def test_bitrix_remote_fields_auth_error(http):
    with respx.mock() as router:
        router.post(f"{HOOK}crm.deal.fields.json").respond(401, json={"error": "invalid_token"})
        with pytest.raises(ConnectorError) as exc:
            await REGISTRY["bitrix24"].remote_fields(make_ctx(http))
    assert "s3cr3tk3y" not in exc.value.message
