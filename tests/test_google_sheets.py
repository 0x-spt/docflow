"""Google Таблицы: токен сервисного аккаунта, проверка, выгрузка без дублей, ошибки доступа и лимитов."""

import json
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
from docflow.schemas.library import builtin_schemas

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
        digital_pdf_url="https://api/v1/documents/1/digital.pdf",
    )


def mock_token(router, expires_in=3600):
    return router.post(TOKEN_URL).respond(json={"access_token": "tok-1", "expires_in": expires_in})


def meta(*titles):
    return {"properties": {"title": "Реестр"}, "sheets": [{"properties": {"title": t}} for t in titles]}


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
            title = r["addSheet"]["properties"]["title"]
            self.titles.append(title)
            self.created.append(title)
        return httpx.Response(200, json={})

    def _read(self, req):
        from urllib.parse import unquote

        raw = unquote(req.url.raw_path.decode().split("/values/")[1])
        sheet = self._sheet_of(req.url.raw_path.decode())
        rows = self.cells[sheet]
        if raw.endswith("A:A"):
            return httpx.Response(200, json={"values": [r[:1] for r in rows]} if rows else {})
        return httpx.Response(200, json={"values": rows[:1]} if rows else {})

    def _append(self, req):
        sheet = self._sheet_of(req.url.raw_path.decode())
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
    assert route.call_count == 2


async def test_test_ok_and_url_id_extraction(http):
    with respx.mock(assert_all_called=False) as router:
        mock_token(router)
        Sheet(router)
        msg = await google_sheets.GoogleSheets().test(
            make_ctx(http, spreadsheet_id=f"https://docs.google.com/spreadsheets/d/{SHEET}/edit#gid=0")
        )
    assert msg == "Таблица «Реестр» доступна"


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
    assert e.value.message == f"нет доступа к таблице — откройте доступ на редактирование для {EMAIL}"


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
    assert row[0] == "11111111-1111-1111-1111-111111111111"
    assert row[2] == "42" and row[3] == "05.10.2026"
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
