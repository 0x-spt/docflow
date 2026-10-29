"""Google Таблицы: выгрузка документов строками в таблицу клиента через сервисный аккаунт.

Доступ: клиент создаёт сервисный аккаунт, открывает ему таблицу на редактирование и вставляет JSON-ключ.
Идемпотентность: id документа ищется в колонке «ID документа» листа «Документы» (в нашей шапке — A) — найден,
значит строку не дублируем. Позиции проверяются отдельно (та же колонка листа «Позиции»), чтобы повтор после сбоя
дописал только недостающее. В шапке клиента без колонки «ID документа» проверки дублей нет.

Шапка: нет — пишем нашу; наша (служебные колонки в начале на местах) с другими подписями полей — переписываем;
своя шапка клиента — не трогаем, значения раскладываются по названиям колонок (`tabular.align_*`).
"""

import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx
import jwt

from docflow.connectors.base import REGISTRY, BaseConnector, ConnectorError, DestCtx, ExportDoc, FieldDef
from docflow.tabular import (
    DOCS_SHEET,
    ITEMS_SHEET,
    Cell,
    align_doc_row,
    align_item_rows,
    clean_text,
    doc_date_columns,
    doc_row,
    docs_header,
    id_column,
    item_date_columns,
    item_rows,
    items_header,
    same_layout,
)

TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPE = "https://www.googleapis.com/auth/spreadsheets"
API = "https://sheets.googleapis.com/v4/spreadsheets"
DATE_FORMAT = "dd.mm.yyyy"
TOKEN_MARGIN = 60  # обновляем токен за минуту до истечения
JWT_LIFETIME = 3600

# (email, отпечаток ключа) -> (токен, монотонное время истечения); в процессе воркера/API
TOKENS: dict[tuple[str, str], tuple[str, float]] = {}

OTHER_COLUMNS = (
    "в этой таблице уже другие колонки — для другого типа документа заведите отдельное подключение или другой лист"
)
NO_KNOWN_COLUMNS = "в таблице нет ни одной знакомой колонки"
READ_ONLY = "у сервисного аккаунта только чтение — откройте доступ на редактирование для {email}"

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


def range_path(sheet: str, cells: str) -> str:
    """Диапазон для пути URL: имя листа может содержать ? # / % — кодируем всё, кроме ' и !."""
    return quote(f"{quote_sheet(sheet)}!{cells}", safe="!'")


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
        if self.docs_sheet.casefold() == self.items_sheet.casefold():
            raise ConnectorError("лист документов и лист позиций должны называться по-разному")

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
        try:
            body = resp.json()
            access = body["access_token"]
            if not isinstance(access, str) or not access:
                raise ValueError
            lifetime = float(body.get("expires_in", 3600))
        except (ValueError, KeyError, TypeError):
            raise _transient("Google вернул непонятный ответ при выдаче доступа") from None
        TOKENS[key] = (access, time.monotonic() + lifetime)
        return access

    async def call(self, method: str, url: str, *, forbidden: str | None = None, **kwargs: Any) -> dict[str, Any]:
        """forbidden — своё сообщение на 403 (когда уже известно, что таблица открыта на чтение)."""
        headers = {"Authorization": f"Bearer {await self.token()}"}
        try:
            resp = await self.http.request(method, url, headers=headers, **kwargs)
        except httpx.HTTPError as exc:
            raise _transient(f"Google Таблицы не ответили ({type(exc).__name__})") from None
        code = resp.status_code
        if code < 300:
            return resp.json() if resp.content else {}
        if code == 403 and forbidden:
            raise ConnectorError(forbidden)
        if code in (403, 404):
            raise ConnectorError(
                "нет доступа к таблице или выключен Google Sheets API — откройте доступ на редактирование "
                f"для {self.email} и проверьте, что API включён"
            )
        if code == 401:
            TOKENS.clear()  # токен мог быть отозван: следующая попытка выпустит новый
            raise _transient("Google отклонил токен, повторим")
        if code == 429 or code >= 500:
            raise _transient(f"Google Таблицы временно недоступны (код {code})")
        raise ConnectorError(f"Google Таблицы отклонили запрос (код {code}): {_api_message(resp)}")

    async def titles(self) -> tuple[str, dict[str, int]]:
        """Название таблицы и листы: название -> sheetId."""
        data = await self.call(
            "GET", self.base, params={"fields": "properties.title,sheets.properties(title,sheetId)"}
        )
        sheets = {
            p.get("title", ""): p.get("sheetId", 0)
            for p in (s.get("properties", {}) for s in data.get("sheets", []))
        }
        return data.get("properties", {}).get("title", ""), sheets

    async def ensure_sheets(self, *wanted: str) -> dict[str, int]:
        _, existing = await self.titles()
        # Google сравнивает названия листов без учёта регистра: «документы» и «Документы» — один лист
        folded = {t.casefold() for t in existing}
        missing = [w for w in dict.fromkeys(wanted) if w.casefold() not in folded]
        if missing:
            await self.call(
                "POST",
                f"{self.base}:batchUpdate",
                json={"requests": [{"addSheet": {"properties": {"title": t}}} for t in missing]},
            )
            _, existing = await self.titles()
        by_fold = {t.casefold(): i for t, i in existing.items()}
        return {w: by_fold[w.casefold()] for w in wanted if w.casefold() in by_fold}

    async def column(self, sheet: str, index: int = 0) -> list[str]:
        letter = column_letter(index)
        data = await self.call(
            "GET",
            f"{self.base}/values/{range_path(sheet, f'{letter}:{letter}')}",
            params={"valueRenderOption": "FORMATTED_VALUE"},
        )
        return [(row[0] if row else "") for row in data.get("values", [])]

    async def read_header(self, sheet: str) -> list[str]:
        """Первая строка листа (пустые ячейки в конце Google не отдаёт)."""
        data = await self.call(
            "GET",
            f"{self.base}/values/{range_path(sheet, '1:1')}",
            params={"valueRenderOption": "FORMATTED_VALUE"},
        )
        return [str(v) for v in (data.get("values") or [[]])[0]]

    async def write_header(self, sheet: str, header: list[str]) -> None:
        await self.call(
            "PUT",
            f"{self.base}/values/{range_path(sheet, 'A1')}",
            params={"valueInputOption": "RAW"},
            json={"values": [header]},
        )

    async def format_dates(self, sheet_id: int, date_columns: list[int]) -> None:
        """Формат ДД.ММ.ГГГГ колонкам с датами (даты уходят серийными числами). Идемпотентно: на каждой записи,
        чтобы формат был и при шапке клиента, и после сбоя между шапкой и форматом."""
        if date_columns:
            fmt = {"numberFormat": {"type": "DATE", "pattern": DATE_FORMAT}}
            requests = [
                {
                    "repeatCell": {
                        "range": {
                            "sheetId": sheet_id,
                            "startRowIndex": 1,
                            "startColumnIndex": col,
                            "endColumnIndex": col + 1,
                        },
                        "cell": {"userEnteredFormat": fmt},
                        "fields": "userEnteredFormat.numberFormat",
                    }
                }
                for col in date_columns
            ]
            await self.call("POST", f"{self.base}:batchUpdate", json={"requests": requests})

    async def append(self, sheet: str, rows: list[list[Cell]]) -> int:
        """Дописывает строки, возвращает номер первой записанной."""
        data = await self.call(
            "POST",
            f"{self.base}/values/{quote(quote_sheet(sheet), safe=chr(39))}:append",
            params={"valueInputOption": "USER_ENTERED", "insertDataOption": "INSERT_ROWS"},
            json={"values": [["" if v is None else v for v in row] for row in rows]},
        )
        return _row_of(data.get("updates", {}).get("updatedRange", ""))


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    out = ""
    index += 1
    while index:
        index, rest = divmod(index - 1, 26)
        out = chr(65 + rest) + out
    return out


def _blank(header: list[str]) -> bool:
    return not any(h.strip() for h in header)


def _ours(header: list[str], mine: list[str], *, lead: int) -> bool:
    """Шапки нет или она наша: служебные колонки в начале на своих местах.

    Решение: «наша» — по всем служебным колонкам в начале, а не только по A1 = «ID документа». Все наши шапки
    всегда начинались так; шапка клиента, где «ID документа» лишь одна из колонок (даже первая), — его, и значения
    раскладываются по названиям (сдвига быть не может). Пустая шапка — когда пуста вся строка 1, а не только A1:
    иначе нашу шапку записали бы поверх названий клиента в B1…
    """
    return _blank(header) or header[:lead] == mine[:lead]


def _renamed_header(existing: list[str], mine: list[str], *, lead: int, tail: int) -> bool:
    """Шапка наша и непустая: та же — False; отличаются только подписи полей — True (переписать);
    иначе строка встала бы со сдвигом колонок — OTHER_COLUMNS."""
    if existing == mine:
        return False
    if not same_layout(existing, mine, lead=lead, tail=tail):
        raise ConnectorError(OTHER_COLUMNS)
    return True


@dataclass
class _Write:
    """Проверенная запись в лист: шапка для A1 (None — не трогаем), строки и колонки с датами."""

    header: list[str] | None
    rows: list[list[Cell]]
    dates: list[int]


def _api_message(resp: httpx.Response) -> str:
    try:
        return str(resp.json()["error"]["message"])[:200]
    except Exception:
        return "без пояснения"


class GoogleSheets(BaseConnector):
    kind = "google_sheets"
    label = "Google Таблицы"
    description = "строка на каждый документ"
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
        # право на запись: записываем таблице её же название — видимых изменений нет, а у читателя будет 403
        # (пустой batchUpdate Google не принимает, а метаданные доступны и на чтение)
        await s.call(
            "POST",
            f"{s.base}:batchUpdate",
            json={"requests": [{"updateSpreadsheetProperties": {"properties": {"title": title}, "fields": "title"}}]},
            forbidden=READ_ONLY.format(email=s.email),
        )
        return f"Таблица «{title}» доступна для записи"

    async def export(self, ctx: DestCtx, doc: ExportDoc) -> str | None:
        """Сначала читаем и проверяем шапки обоих листов, потом пишем: ошибка шапки — без частичной записи.

        Позиции пишутся до строки документа. Если у листа позиций нет колонки «ID документа», дубли позиций
        ловятся только так: строка документа уже есть — значит, позиции записаны раньше, пропускаем всё.
        Сбой между позициями и строкой документа даст при повторе дубль позиций — заметно, но ничего не теряется.
        """
        s = _Session(ctx)
        doc_id = str(doc.id)
        items = list(item_rows(doc, target="sheets"))
        ids = await s.ensure_sheets(s.docs_sheet, *([s.items_sheet] if items else []))

        header = await s.read_header(s.docs_sheet)
        mine = docs_header(doc.schema, ctx.mapping)
        docs_ours = _ours(header, mine, lead=4)
        id_col = 0 if docs_ours else id_column(header)
        column = await s.column(s.docs_sheet, id_col) if id_col is not None else []
        found = doc_id in column
        # строку документа проверяем до записи позиций
        docs_write = None if found else await self._docs_write(s, doc, ctx.mapping, header, mine, docs_ours)

        items_write = None
        if items:
            items_header_row = await s.read_header(s.items_sheet)
            items_mine = items_header(doc.schema)
            items_ours = _ours(items_header_row, items_mine, lead=2)
            items_id = 0 if items_ours else id_column(items_header_row)
            if items_id is not None:
                done = doc_id in await s.column(s.items_sheet, items_id)
            else:
                done = found  # позиции пишутся раньше документа: документ на месте — и позиции тоже
            if not done:
                if items_ours:
                    rewrite = not _blank(items_header_row) and _renamed_header(
                        items_header_row, items_mine, lead=2, tail=0
                    )
                    if rewrite:
                        # у листа позиций нет колонки «Тип»: переписываем, только если лист документов наш
                        # и в нём документы этого типа — иначе подписи легли бы на позиции другого типа
                        if not docs_ours or _blank(header):
                            raise ConnectorError(OTHER_COLUMNS)
                        await self._same_type(s, doc)
                    head = items_mine if _blank(items_header_row) or rewrite else None
                    items_write = _Write(head, items, item_date_columns(doc.schema))
                else:
                    aligned = align_item_rows(doc, items_header_row, target="sheets")
                    if aligned is None:
                        raise ConnectorError(NO_KNOWN_COLUMNS)
                    items_write = _Write(None, *aligned)

        # все проверки позади — запись
        if items_write:
            await self._write(s, s.items_sheet, ids[s.items_sheet], items_write)
        if docs_write:
            row = await self._write(s, s.docs_sheet, ids[s.docs_sheet], docs_write)
        else:
            row = column.index(doc_id) + 1
        return f"{s.docs_sheet}!A{row}"

    async def _docs_write(
        self,
        s: _Session,
        doc: ExportDoc,
        mapping: dict[str, str],
        header: list[str],
        mine: list[str],
        ours: bool,
    ) -> _Write:
        if not ours:
            aligned = align_doc_row(doc, header, mapping, target="sheets")
            if aligned is None:
                raise ConnectorError(NO_KNOWN_COLUMNS)
            # колонки ID нет — дублей не проверить: повтор после потерянного ответа может дописать строку
            return _Write(None, [aligned[0]], aligned[1])
        if _blank(header):
            head: list[str] | None = mine
        elif _renamed_header(header, mine, lead=4, tail=3):
            await self._same_type(s, doc)
            head = mine
        else:
            head = None
        return _Write(head, [doc_row(doc, mapping, target="sheets")], doc_date_columns(doc.schema, mapping))

    @staticmethod
    async def _write(s: _Session, sheet: str, sheet_id: int, w: _Write) -> int:
        if w.header is not None:
            await s.write_header(sheet, w.header)
        await s.format_dates(sheet_id, w.dates)
        return await s.append(sheet, w.rows)

    @staticmethod
    async def _same_type(s: _Session, doc: ExportDoc) -> None:
        """Перед тем как переписать нашу шапку: в листе документов только документы этого типа (колонка «Тип»).

        Решение (сверх плана): та же длина шапки у другой схемы проходит same_layout, и подписи легли бы на
        колонки прежних строк с другим смыслом. Лишний GET — только когда шапку переписываем."""
        title = clean_text(doc.title)
        if any(t.strip() and t != title for t in (await s.column(s.docs_sheet, 1))[1:]):
            raise ConnectorError(OTHER_COLUMNS)


REGISTRY["google_sheets"] = GoogleSheets()

__all__ = ["NO_KNOWN_COLUMNS", "TOKENS", "GoogleSheets", "extract_spreadsheet_id", "parse_service_account"]
