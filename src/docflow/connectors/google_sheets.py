"""Google Таблицы: выгрузка документов строками в таблицу клиента через сервисный аккаунт.

Доступ: клиент создаёт сервисный аккаунт, открывает ему таблицу на редактирование и вставляет JSON-ключ.
Идемпотентность: id документа ищется в колонке A листа «Документы» — найден, значит строку не дублируем.
Позиции проверяются отдельно (колонка A листа «Позиции»), чтобы повтор после сбоя дописал только недостающее.
"""

import hashlib
import json
import re
import time
from typing import Any

import httpx
import jwt

from docflow.connectors.base import REGISTRY, BaseConnector, ConnectorError, DestCtx, ExportDoc, FieldDef
from docflow.tabular import DOCS_SHEET, ITEMS_SHEET, Cell, doc_row, docs_header, item_rows, items_header

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
API = "https://sheets.googleapis.com/v4/spreadsheets"
TOKEN_MARGIN = 60  # обновляем токен за минуту до истечения
JWT_LIFETIME = 3600

# (email, отпечаток ключа) -> (токен, монотонное время истечения); в процессе воркера/API
TOKENS: dict[tuple[str, str], tuple[str, float]] = {}

_SHEET_ID = re.compile(r"/spreadsheets/d/([A-Za-z0-9_-]+)")


def _transient(message: str) -> ConnectorError:
    return ConnectorError(message, retryable=True)


def parse_service_account(raw: Any) -> dict[str, str]:
    """JSON ключа сервисного аккаунта: нужны client_email и private_key. ConnectorError(не retryable) иначе."""
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except ValueError:
        raise ConnectorError("ключ сервисного аккаунта — не JSON: вставьте файл ключа целиком") from None
    if not isinstance(data, dict):
        raise ConnectorError("ключ сервисного аккаунта должен быть JSON-объектом")
    for key in ("client_email", "private_key"):
        if not isinstance(data.get(key), str) or not data[key].strip():
            raise ConnectorError(f"в ключе сервисного аккаунта нет поля {key}")
    return data


def extract_spreadsheet_id(value: Any) -> str:
    text = str(value or "").strip()
    found = _SHEET_ID.search(text)
    sid = found.group(1) if found else text
    if not re.fullmatch(r"[A-Za-z0-9_-]+", sid):
        raise ConnectorError("не удалось определить ID таблицы — вставьте ссылку на таблицу или её ID")
    return sid


def quote_sheet(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _row_of(updated_range: str) -> int:
    found = re.search(r"!\$?[A-Z]+(\d+)", updated_range)
    if not found:
        raise _transient("Google не сообщил, в какую строку записан документ")
    return int(found.group(1))


class _Session:
    """Один вызов коннектора: токен, разобранные настройки и запросы с переводом ошибок."""

    def __init__(self, ctx: DestCtx):
        self.http = ctx.http
        self.account = parse_service_account(ctx.secrets.get("service_account_json"))
        self.email = self.account["client_email"]
        self.base = f"{API}/{extract_spreadsheet_id(ctx.config.get('spreadsheet_id'))}"
        self.docs_sheet = str(ctx.config.get("documents_sheet") or "").strip() or DOCS_SHEET
        self.items_sheet = str(ctx.config.get("items_sheet") or "").strip() or ITEMS_SHEET

    async def token(self) -> str:
        key = (self.email, hashlib.sha256(self.account["private_key"].encode()).hexdigest())
        cached = TOKENS.get(key)
        if cached and cached[1] - TOKEN_MARGIN > time.monotonic():
            return cached[0]
        now = int(time.time())
        try:
            assertion = jwt.encode(
                {"iss": self.email, "scope": SCOPE, "aud": TOKEN_URL, "iat": now, "exp": now + JWT_LIFETIME},
                self.account["private_key"],
                algorithm="RS256",
            )
        except Exception:
            raise ConnectorError("не удалось подписать запрос: проверьте private_key в ключе") from None
        try:
            resp = await self.http.post(
                TOKEN_URL,
                data={"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": assertion},
            )
        except httpx.HTTPError as exc:
            raise _transient(f"Google не ответил при выдаче доступа ({type(exc).__name__})") from None
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _transient(f"Google временно недоступен (код {resp.status_code})")
        if resp.status_code != 200:
            raise ConnectorError("Google отклонил ключ сервисного аккаунта — создайте и вставьте ключ заново")
        body = resp.json()
        TOKENS[key] = (body["access_token"], time.monotonic() + float(body.get("expires_in", 3600)))
        return body["access_token"]

    async def call(self, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {await self.token()}"}
        try:
            resp = await self.http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise _transient(f"Google Таблицы не ответили ({type(exc).__name__})") from None
        code = resp.status_code
        if code < 300:
            return resp.json() if resp.content else {}
        if code in (403, 404):
            raise ConnectorError(f"нет доступа к таблице — откройте доступ на редактирование для {self.email}")
        if code == 401:
            TOKENS.clear()  # токен мог быть отозван: следующая попытка выпустит новый
            raise _transient("Google отклонил токен, повторим")
        if code == 429 or code >= 500:
            raise _transient(f"Google Таблицы временно недоступны (код {code})")
        raise ConnectorError(f"Google Таблицы отклонили запрос (код {code}): {_api_message(resp)}")

    async def titles(self) -> tuple[str, list[str]]:
        data = await self.call("GET", self.base, params={"fields": "properties.title,sheets.properties.title"})
        sheets = [s.get("properties", {}).get("title", "") for s in data.get("sheets", [])]
        return data.get("properties", {}).get("title", ""), sheets

    async def ensure_sheets(self, *wanted: str) -> None:
        _, existing = await self.titles()
        missing = [w for w in dict.fromkeys(wanted) if w not in existing]
        if missing:
            await self.call(
                "POST",
                f"{self.base}:batchUpdate",
                json={"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]},
            )

    async def column_a(self, sheet: str) -> list[str]:
        data = await self.call("GET", f"{self.base}/values/{quote_sheet(sheet)}!A:A")
        return [(row[0] if row else "") for row in data.get("values", [])]

    async def ensure_header(self, sheet: str, column: list[str], header: list[str]) -> None:
        if column and column[0] != "":
            return  # шапка есть (наша или клиента) — не трогаем
        await self.call(
            "PUT",
            f"{self.base}/values/{quote_sheet(sheet)}!A1",
            params={"valueInputOption": "RAW"},
            json={"values": [header]},
        )

    async def append(self, sheet: str, rows: list[list[Cell]]) -> int:
        """Дописывает строки, возвращает номер первой записанной."""
        data = await self.call(
            "POST",
            f"{self.base}/values/{quote_sheet(sheet)}:append",
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": [["" if v is None else v for v in row] for row in rows]},
        )
        return _row_of(data.get("updates", {}).get("updatedRange", ""))


def _api_message(resp: httpx.Response) -> str:
    try:
        return str(resp.json()["error"]["message"])[:200]
    except Exception:
        return "без пояснения"


class GoogleSheets(BaseConnector):
    kind = "google_sheets"
    label = "Google Таблицы"
    config_fields = [
        FieldDef(
            "spreadsheet_id",
            "Таблица",
            required=True,
            help="Ссылка на таблицу или её ID. Откройте доступ на редактирование для email сервисного аккаунта.",
        ),
        FieldDef("documents_sheet", "Лист документов", help=f"По умолчанию «{DOCS_SHEET}»; создаётся, если нет."),
        FieldDef("items_sheet", "Лист позиций", help=f"По умолчанию «{ITEMS_SHEET}»; создаётся, если нет."),
    ]
    secret_fields = [
        FieldDef(
            "service_account_json",
            "Ключ сервисного аккаунта (JSON)",
            required=True,
            type="textarea",
            help="Файл ключа сервисного аккаунта Google Cloud целиком.",
        ),
    ]
    target_fields: list[FieldDef] = []

    async def test(self, ctx: DestCtx) -> str | None:
        s = _Session(ctx)
        title, _ = await s.titles()
        return f"Таблица «{title}» доступна"

    async def export(self, ctx: DestCtx, doc: ExportDoc) -> str | None:
        s = _Session(ctx)
        doc_id = str(doc.id)
        items = list(item_rows(doc))
        await s.ensure_sheets(s.docs_sheet, *([s.items_sheet] if items else []))

        column = await s.column_a(s.docs_sheet)
        if doc_id in column:
            row = column.index(doc_id) + 1
        else:
            await s.ensure_header(s.docs_sheet, column, docs_header(doc.schema, ctx.mapping))
            row = await s.append(s.docs_sheet, [doc_row(doc, ctx.mapping)])

        if items:
            items_col = await s.column_a(s.items_sheet)
            if doc_id not in items_col:  # повтор после сбоя дописывает только недостающее
                await s.ensure_header(s.items_sheet, items_col, items_header(doc.schema))
                await s.append(s.items_sheet, items)
        return f"{s.docs_sheet}!A{row}"


REGISTRY["google_sheets"] = GoogleSheets()

__all__ = ["TOKENS", "GoogleSheets", "extract_spreadsheet_id", "parse_service_account"]
