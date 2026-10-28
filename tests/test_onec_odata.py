"""1С через OData: проверка публикации, контрагент по ИНН/названию, документ с позициями без дублей, ошибки."""

import base64
import json
import re
import uuid
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlsplit

import httpx
import pytest
import respx

from docflow.connectors.base import REGISTRY, ConnectorError, DestCtx, ExportDoc
from docflow.schemas.library import builtin_schemas

ROOT = "https://1c.example.ru/base/odata/standard.odata/"
DOC_ID = "11111111-1111-1111-1111-111111111111"
ORG = "aaaaaaaa-0000-0000-0000-000000000001"
PASSWORD = "p@ss-s3cr3t"
PURCHASE = "Document_ПоступлениеТоваровУслуг"
SALE = "Document_РеализацияТоваровУслуг"
PARTNERS = "Catalog_Контрагенты"
GOODS = "Catalog_Номенклатура"

METADATA = """<?xml version="1.0" encoding="UTF-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="1.0">
<edmx:DataServices><Schema xmlns="http://schemas.microsoft.com/ado/2009/11/edm" Namespace="StandardODATA">
<EntityContainer Name="InfoBase" m:IsDefaultEntityContainer="true">
{sets}
</EntityContainer></Schema></edmx:DataServices></edmx:Edmx>"""
ALL_SETS = [PARTNERS, GOODS, PURCHASE, f"{PURCHASE}_Товары", SALE, f"{SALE}_Товары"]


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make_ctx(http, mapping=None, secrets=None, **config) -> DestCtx:
    cfg = {"base_url": "https://1c.example.ru/base", "organization_key": ORG, **config}
    return DestCtx(
        config=cfg,
        secrets={"username": "Бухгалтер", "password": PASSWORD} if secrets is None else secrets,
        mapping=mapping or {},
        http=http,
    )


def make_doc(schema="invoice_ru", **data) -> ExportDoc:
    s = builtin_schemas()[schema]
    return ExportDoc(
        id=uuid.UUID(DOC_ID),
        tenant_id=uuid.uuid4(),
        schema=s,
        data={
            "number": "42",
            "issued": "2026-10-05",
            "supplier": "ООО Ромашка",
            "supplier_inn": "7707083893",
            "supplier_kpp": "770701001",
            "buyer": "ООО Лютик",
            "buyer_inn": "5003052454",
            "items": [
                {"name": "Бумага А4", "qty": 2, "unit": "шт", "price": 500, "amount": 1000},
                {"name": "Ручка", "qty": 10, "unit": "шт", "price": 22, "amount": 220},
            ],
            "total": 1220,
            **data,
        },
        status="ok",
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        title=s.title,
        number=data.get("number", "42"),
        issued=datetime(2026, 10, 5).date(),
        cabinet_url="https://site/app/documents/1",
    )


def _literal(flt: str, pattern: str) -> str | None:
    m = re.search(pattern, flt)
    return m.group(1).replace("''", "'") if m else None


class OneC:
    """Имитация стандартного интерфейса OData 1С: справочники и документы в памяти, JSON как у 1С."""

    def __init__(self, router, partners=None, goods=None, docs=None, sets=None):
        self.partners: list[dict] = list(partners or [])
        self.goods: list[dict] = list(goods or [{"Description": "Бумага А4"}, {"Description": "Ручка"}])
        for g in self.goods:
            g.setdefault("Ref_Key", str(uuid.uuid4()))
        self.docs: dict[str, list[dict]] = {PURCHASE: list(docs or []), SALE: []}
        self.sets = ALL_SETS if sets is None else sets
        self.calls: list[tuple[str, str, dict, object]] = []  # method, entity, query, body
        self.raw_urls: list[str] = []
        self.auth: list[str] = []
        self.fail: dict[tuple[str, str], httpx.Response] = {}
        router.route(url__startswith=ROOT).mock(side_effect=self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.raw_urls.append(str(request.url))
        self.auth.append(request.headers.get("authorization", ""))
        entity = unquote(request.url.raw_path.decode().split("?")[0].removeprefix(urlsplit(ROOT).path))
        query = {k: v[0] for k, v in parse_qs(urlsplit(str(request.url)).query).items()}
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, entity, query, body))
        if (request.method, entity) in self.fail:
            return self.fail[(request.method, entity)]
        if entity == "$metadata":
            sets = "\n".join(f'<EntitySet Name="{s}" EntityType="StandardODATA.{s}"/>' for s in self.sets)
            return httpx.Response(200, text=METADATA.format(sets=sets), headers={"content-type": "application/xml"})
        if entity not in self.sets:
            return httpx.Response(404, json={"odata.error": {"code": "", "message": {"lang": "ru", "value": "Нет"}}})
        if query.get("$format") != "json":
            return httpx.Response(400, text="<xml/>")
        store = {PARTNERS: self.partners, GOODS: self.goods}.get(entity)
        if store is None:
            store = self.docs[entity]
        if request.method == "POST":
            obj = {"Ref_Key": str(uuid.uuid4()), **body}
            store.append(obj)
            return httpx.Response(201, json={"odata.metadata": "x", **obj})
        flt = query.get("$filter", "")
        rows = [r for r in store if self._match(r, flt)]
        if "$top" in query:
            rows = rows[: int(query["$top"])]
        return httpx.Response(200, json={"odata.metadata": "x", "value": rows})

    @staticmethod
    def _match(row: dict, flt: str) -> bool:
        if (inn := _literal(flt, r"ИНН eq '((?:[^']|'')*)'")) is not None and row.get("ИНН") != inn:
            return False
        if (name := _literal(flt, r"Description eq '((?:[^']|'')*)'")) is not None and row.get("Description") != name:
            return False
        sub = _literal(flt, r"substringof\('((?:[^']|'')*)', Description\)")
        if sub is not None and sub not in row.get("Description", ""):
            return False
        mark = _literal(flt, r"substringof\('((?:[^']|'')*)', Комментарий\)")
        if mark is not None and mark not in row.get("Комментарий", ""):
            return False
        if "DeletionMark eq false" in flt and row.get("DeletionMark"):
            return False
        return True

    def posts(self, entity):
        return [b for m, e, _, b in self.calls if m == "POST" and e == entity]

    def gets(self, entity):
        return [q for m, e, q, _ in self.calls if m == "GET" and e == entity]


async def test_registered_and_fields():
    c = REGISTRY["onec_odata"]
    assert {f.name for f in c.secret_fields} == {"username", "password"}
    by_name = {f.name: f for f in c.config_fields}
    assert by_name["base_url"].type == "url" and by_name["base_url"].required
    assert by_name["organization_key"].required and "Ref_Key" in by_name["organization_key"].help
    assert by_name["document_kind"].options == ["purchase", "sale"]
    assert by_name["vat_rate"].options == ["НДС22", "НДС20", "НДС10", "НДС0", "БезНДС"]
    assert {"counterparty", "counterparty_inn", "incoming_number", "item_name"} <= {f.name for f in c.target_fields}


async def test_test_reads_metadata_and_checks_objects(http):
    with respx.mock() as router:
        one = OneC(router)
        msg = await REGISTRY["onec_odata"].test(make_ctx(http))
    assert msg == "Публикация 1С доступна, объекты найдены"
    assert [(m, e) for m, e, _, _ in one.calls] == [("GET", "$metadata")]
    assert one.auth[0] == "Basic " + base64.b64encode(f"Бухгалтер:{PASSWORD}".encode()).decode()


@pytest.mark.parametrize("base", ["https://1c.example.ru/base", "https://1c.example.ru/base/", ROOT, ROOT.rstrip("/")])
async def test_base_url_normalised(http, base):
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].test(make_ctx(http, base_url=base))
    assert one.raw_urls[0] == ROOT + "$metadata"


async def test_test_names_missing_objects(http):
    with respx.mock() as router:
        OneC(router, sets=[PARTNERS, SALE])
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].test(make_ctx(http))
    assert not err.value.retryable
    assert GOODS in err.value.message and PURCHASE in err.value.message and PARTNERS not in err.value.message


async def test_export_creates_counterparty_and_document(http):
    with respx.mock() as router:
        one = OneC(router)
        ext = await REGISTRY["onec_odata"].export(make_ctx(http, warehouse_key=ORG.replace("1", "2")), make_doc())
    partner = one.posts(PARTNERS)[0]
    assert partner["Description"] == "ООО Ромашка" and partner["НаименованиеПолное"] == "ООО Ромашка"
    assert partner["ИНН"] == "7707083893" and partner["КПП"] == "770701001"
    assert partner["ЮридическоеФизическоеЛицо"] == "ЮридическоеЛицо"
    [doc] = one.posts(PURCHASE)
    assert ext == one.docs[PURCHASE][0]["Ref_Key"]
    assert doc["Date"] == "2026-10-05T00:00:00"
    assert doc["Организация_Key"] == ORG and doc["Контрагент_Key"] == one.partners[0]["Ref_Key"]
    assert doc["Склад_Key"] == ORG.replace("1", "2") and "ДоговорКонтрагента_Key" not in doc
    assert doc["ВидОперации"] == "Товары"
    assert doc["НомерВходящегоДокумента"] == "42" and doc["ДатаВходящегоДокумента"] == "2026-10-05T00:00:00"
    assert doc["Комментарий"] == f"DocFlow docflow:{DOC_ID} · https://site/app/documents/1"
    assert doc["ЦенаВключаетНДС"] is True and doc["СуммаДокумента"] == 1220
    rows = doc["Товары"]
    assert rows[0] == {
        "LineNumber": "1",
        "Номенклатура_Key": one.goods[0]["Ref_Key"],
        "Количество": 2,
        "Цена": 500,
        "Сумма": 1000,
        "СтавкаНДС": "НДС22",
        "СуммаНДС": 180.33,  # 1000 * 22 / 122
    }
    assert rows[1]["LineNumber"] == "2" and rows[1]["Номенклатура_Key"] == one.goods[1]["Ref_Key"]
    assert rows[1]["СуммаНДС"] == 39.67
    # без проводки: только поиск и создание
    assert [m for m, *_ in one.calls].count("POST") == 2
    assert not any("Post" in e for _, e, _, _ in one.calls)


async def test_odata_query_format(http):
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    first = one.gets(PURCHASE)[0]
    assert first["$filter"] == f"substringof('docflow:{DOC_ID}', Комментарий)"
    assert first["$format"] == "json" and first["$select"] == "Ref_Key,Number"
    assert any("ИНН eq '7707083893'" in q["$filter"] for q in one.gets(PARTNERS))
    assert all("+" not in u.split("?", 1)[1] for u in one.raw_urls if "?" in u)  # пробелы — %20, не «+»


async def test_counterparty_found_by_inn(http):
    key = str(uuid.uuid4())
    with respx.mock() as router:
        one = OneC(router, partners=[{"Ref_Key": key, "Description": "Ромашка", "ИНН": "7707083893", "КПП": ""}])
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert not one.posts(PARTNERS)
    assert one.posts(PURCHASE)[0]["Контрагент_Key"] == key


async def test_counterparty_by_inn_prefers_matching_kpp(http):
    a, b = str(uuid.uuid4()), str(uuid.uuid4())
    partners = [
        {"Ref_Key": a, "Description": "Ромашка (филиал)", "ИНН": "7707083893", "КПП": "770702002"},
        {"Ref_Key": b, "Description": "Ромашка", "ИНН": "7707083893", "КПП": "770701001"},
    ]
    with respx.mock() as router:
        one = OneC(router, partners=partners)
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert one.posts(PURCHASE)[0]["Контрагент_Key"] == b


async def test_counterparty_without_inn_by_name_then_created(http):
    key = str(uuid.uuid4())
    with respx.mock() as router:
        one = OneC(router, partners=[{"Ref_Key": key, "Description": "ООО Ромашка"}])
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc(supplier_inn=None, supplier_kpp=None))
    assert not one.posts(PARTNERS) and one.posts(PURCHASE)[0]["Контрагент_Key"] == key

    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc(supplier_inn=None, supplier_kpp=None))
    [partner] = one.posts(PARTNERS)
    assert partner["Description"] == "ООО Ромашка" and "ИНН" not in partner and "КПП" not in partner


async def test_entrepreneur_inn_is_individual(http):
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(
            make_ctx(http), make_doc(supplier="ИП Иванов И. И.", supplier_inn="500100732259", supplier_kpp="770701001")
        )
    partner = one.posts(PARTNERS)[0]
    assert partner["ЮридическоеФизическоеЛицо"] == "ФизическоеЛицо" and "КПП" not in partner


async def test_no_counterparty_at_all_is_permanent_error(http):
    with respx.mock(assert_all_called=False) as router:
        one = OneC(router)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(
                make_ctx(http), make_doc(supplier=None, supplier_inn=None, supplier_kpp=None)
            )
    assert not err.value.retryable and "контрагент" in err.value.message
    assert not one.posts(PURCHASE)


async def test_repeat_export_finds_existing_document(http):
    with respx.mock() as router:
        one = OneC(router)
        first = await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
        one.calls.clear()
        second = await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert first == second and len(one.docs[PURCHASE]) == 1
    assert [(m, e) for m, e, _, _ in one.calls] == [("GET", PURCHASE)]


async def test_retry_after_timeout_on_document_post_no_duplicate(http):
    """Таймаут ответа на создание: 1С документ записала, повтор находит его по комментарию."""
    with respx.mock() as router:
        one = OneC(router)
        original = one.handle

        def lose_reply(request):
            resp = original(request)
            if request.method == "POST" and PURCHASE in unquote(request.url.path):
                raise httpx.ReadTimeout("timeout")
            return resp

        router.routes.clear()
        router.route(url__startswith=ROOT).mock(side_effect=lose_reply)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
        assert err.value.retryable
        ext = await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert len(one.docs[PURCHASE]) == 1 and ext == one.docs[PURCHASE][0]["Ref_Key"]
    assert len(one.partners) == 1  # контрагент тоже не задвоился


async def test_missing_nomenclature_is_permanent_and_creates_nothing(http):
    with respx.mock() as router:
        one = OneC(router, goods=[{"Description": "Бумага А4"}])
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert not err.value.retryable
    assert err.value.message == ("в 1С нет номенклатуры: «Ручка» — заведите её или добавьте сопоставление")
    assert not one.posts(PURCHASE) and not one.posts(PARTNERS)


async def test_missing_nomenclature_lists_three(http):
    items = [{"name": f"Товар {i}", "qty": 1, "price": 1, "amount": 1} for i in range(5)]
    with respx.mock() as router:
        OneC(router, goods=[])
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc(items=items))
    assert "«Товар 0», «Товар 1», «Товар 2» и ещё 2" in err.value.message


async def test_nomenclature_fallback_substring_single_match(http):
    goods = [
        {"Description": "Бумага А4 SvetoCopy"},
        {"Description": "Ручка шариковая синяя"},
        {"Description": "Ручка гелевая"},
    ]
    with respx.mock() as router:
        one = OneC(router, goods=goods)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert "«Ручка»" in err.value.message and "Бумага" not in err.value.message  # две ручки — неоднозначно
    subs = [q for q in one.gets(GOODS) if "substringof" in q["$filter"]]
    assert subs and all(q["$top"] == "2" for q in subs)


async def test_nomenclature_name_quotes_escaped(http):
    items = [{"name": 'Бумага "Снегурочка" д\'Артаньян', "qty": 1, "price": 5, "amount": 5}]
    with respx.mock() as router:
        one = OneC(router, goods=[{"Description": 'Бумага "Снегурочка" д\'Артаньян'}])
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc(items=items))
    assert "д''Артаньян" in one.gets(GOODS)[0]["$filter"]
    assert one.posts(PURCHASE)[0]["Товары"][0]["Номенклатура_Key"] == one.goods[0]["Ref_Key"]


async def test_document_without_items(http):
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc(items=[]))
    doc = one.posts(PURCHASE)[0]
    assert "Товары" not in doc and "позиции не распознаны" in doc["Комментарий"]
    assert not one.gets(GOODS)


async def test_sale_document_uses_buyer_and_no_incoming_number(http):
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(
            make_ctx(http, document_kind="sale", vat_rate="БезНДС", contract_key=ORG), make_doc()
        )
    assert one.posts(PARTNERS)[0]["ИНН"] == "5003052454"
    [doc] = one.posts(SALE)
    assert "НомерВходящегоДокумента" not in doc and doc["ДоговорКонтрагента_Key"] == ORG
    assert doc["Товары"][0]["СтавкаНДС"] == "БезНДС" and doc["Товары"][0]["СуммаНДС"] == 0
    assert not one.posts(PURCHASE)


async def test_upd_amounts_without_vat(http):
    items = [{"name": "Бумага А4", "qty": 2, "price": 500, "amount": 1000, "vat_amount": 220, "amount_with_vat": 1220}]
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc("upd_ru", items=items, total=1220, vat=220))
    doc = one.posts(PURCHASE)[0]
    assert doc["ЦенаВключаетНДС"] is False and doc["СуммаДокумента"] == 1220
    assert doc["Товары"][0]["Сумма"] == 1000 and doc["Товары"][0]["СуммаНДС"] == 220


async def test_invoice_vat_on_top(http):
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(make_ctx(http), make_doc(subtotal=1220, total=1488.4, vat=268.4))
    doc = one.posts(PURCHASE)[0]
    assert doc["ЦенаВключаетНДС"] is False and doc["СуммаДокумента"] == 1488.4
    assert doc["Товары"][0]["СуммаНДС"] == 220


async def test_mapping_overrides_sources(http):
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(
            make_ctx(
                http, mapping={"buyer": "counterparty", "buyer_inn": "counterparty_inn", "bik": "incoming_number"}
            ),
            make_doc(bik="044525225"),
        )
    assert one.posts(PARTNERS)[0]["Description"] == "ООО Лютик"
    assert one.posts(PURCHASE)[0]["НомерВходящегоДокумента"] == "044525225"


async def test_unknown_mapping_target_rejected(http):
    with respx.mock(assert_all_called=False) as router:
        OneC(router)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http, mapping={"buyer": "Склад"}), make_doc())
    assert not err.value.retryable and "Склад" in err.value.message


async def test_custom_object_and_field_names(http):
    with respx.mock() as router:
        one = OneC(router, sets=[*ALL_SETS, "Catalog_Партнеры"])
        one.docs.setdefault("Catalog_Партнеры", [])
        one.partners = one.docs["Catalog_Партнеры"]
        await REGISTRY["onec_odata"].export(
            make_ctx(
                http, counterparty_object="Catalog_Партнеры", field_names="Комментарий = Примечание\nВидОперации=Вид"
            ),
            make_doc(),
        )
    doc = one.posts(PURCHASE)[0]
    assert "Примечание" in doc and "Комментарий" not in doc and doc["Вид"] == "Товары"
    assert one.posts("Catalog_Партнеры") and not one.posts(PARTNERS)
    assert "Примечание" in one.gets(PURCHASE)[0]["$filter"]


@pytest.mark.parametrize("code", [401, 403])
async def test_auth_errors_permanent_without_secrets(http, code):
    with respx.mock() as router:
        OneC(router).fail[("GET", PURCHASE)] = httpx.Response(code, text="Unauthorized")
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert not err.value.retryable
    assert err.value.message == "1С не приняла логин или пароль (или у пользователя нет прав на OData)"


async def test_404_names_object(http):
    with respx.mock() as router:
        OneC(router, sets=[PARTNERS, GOODS])
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert not err.value.retryable
    assert err.value.message == (
        f"в публикации OData нет объекта {PURCHASE} — включите его в составе стандартного интерфейса OData"
    )


async def test_400_odata_error_message(http):
    with respx.mock() as router:
        OneC(router).fail[("POST", PURCHASE)] = httpx.Response(
            400, json={"odata.error": {"code": "15", "message": {"lang": "ru", "value": "Неверный тип\x07 поля Date"}}}
        )
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert not err.value.retryable and "Неверный тип поля Date" in err.value.message


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_transient_errors_retryable(http, code):
    with respx.mock() as router:
        OneC(router).fail[("GET", PURCHASE)] = httpx.Response(code, text="busy")
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert err.value.retryable and PASSWORD not in err.value.message


async def test_connection_error_retryable(http):
    with respx.mock() as router:
        router.route(url__startswith=ROOT).mock(side_effect=httpx.ConnectError("refused"))
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].test(make_ctx(http))
    assert err.value.retryable and "1c.example.ru" not in err.value.message


async def test_credentials_in_url_never_in_message(http):
    with respx.mock(assert_all_called=False) as router:
        router.route(url__startswith="https://user:").mock(return_value=httpx.Response(500))
        router.route(url__startswith="https://1c.example.ru/").mock(return_value=httpx.Response(500))
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].test(make_ctx(http, base_url=f"https://user:{PASSWORD}@1c.example.ru/base"))
    assert PASSWORD not in err.value.message and "user" not in err.value.message


@pytest.mark.parametrize(
    ("config", "secrets", "needle"),
    [
        ({"organization_key": "Моя организация"}, None, "Организация"),
        ({"warehouse_key": "склад"}, None, "Склад"),
        ({"document_kind": "returns"}, None, "Вид документа"),
        ({"vat_rate": "НДС18"}, None, "Ставка НДС"),
        ({"field_names": "Нечто=Другое"}, None, "Нечто"),
        ({}, {"password": "x"}, "логин"),
    ],
)
async def test_bad_settings_are_permanent(http, config, secrets, needle):
    with respx.mock(assert_all_called=False) as router:
        one = OneC(router)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http, secrets=secrets, **config), make_doc())
    assert not err.value.retryable and needle in err.value.message
    assert not one.posts(PURCHASE)


async def test_html_answer_is_permanent(http):
    with respx.mock() as router:
        OneC(router).fail[("GET", PURCHASE)] = httpx.Response(200, text="<html>login</html>")
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc())
    assert not err.value.retryable and "JSON" in err.value.message


async def test_row_derivations(http):
    items = [
        {"name": "Бумага А4", "qty": None, "price": None, "amount": 300},
        {"name": "Ручка", "qty": 3, "price": 10, "amount": None},
        {"name": None, "qty": None, "price": None, "amount": None},
    ]
    with respx.mock() as router:
        one = OneC(router)
        await REGISTRY["onec_odata"].export(make_ctx(http, vat_rate="НДС10"), make_doc(items=items, total=330))
    rows = one.posts(PURCHASE)[0]["Товары"]
    assert len(rows) == 2
    assert rows[0]["Количество"] == 1 and rows[0]["Цена"] == 300 and rows[0]["СуммаНДС"] == 27.27
    assert rows[1]["Сумма"] == 30 and rows[1]["СтавкаНДС"] == "НДС10"


async def test_row_without_name_is_permanent_error(http):
    items = [{"name": "", "qty": 1, "price": 5, "amount": 5}]
    with respx.mock(assert_all_called=False) as router:
        OneC(router)
        with pytest.raises(ConnectorError) as err:
            await REGISTRY["onec_odata"].export(make_ctx(http), make_doc(items=items))
    assert not err.value.retryable and "строка 1" in err.value.message
