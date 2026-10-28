"""1С через стандартный интерфейс OData: поступление (или реализация) с позициями и контрагентом.

Расширение в базе клиента не нужно: администратор публикует базу на веб-сервере и включает объекты в состав
стандартного интерфейса OData. Доступ — пользователь 1С (Basic). Имена объектов и реквизитов по умолчанию —
«Бухгалтерия предприятия 3.0», меняются в настройках. Документ создаётся непроведённым: бухгалтер проверяет и проводит.

Идемпотентность: в «Комментарий» пишем `docflow:<id документа>` и перед созданием ищем документ по нему; контрагент
находится повторно по ИНН (или по названию), номенклатура только ищется — её не создаём.
"""

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from docflow.connectors.base import REGISTRY, BaseConnector, ConnectorError, DestCtx, ExportDoc, FieldDef
from docflow.connectors.crm_common import Party, clean, marker, party_from, total_of
from docflow.tabular import table_field

ODATA_SUFFIX = "/odata/standard.odata"
DOC_OBJECTS = {"purchase": "Document_ПоступлениеТоваровУслуг", "sale": "Document_РеализацияТоваровУслуг"}
COUNTERPARTY_OBJECT = "Catalog_Контрагенты"
NOMENCLATURE_OBJECT = "Catalog_Номенклатура"
VAT_RATES = {"НДС22": 22, "НДС20": 20, "НДС10": 10, "НДС0": 0, "БезНДС": 0}
VAT_LABELS = {"НДС22": "НДС 22%", "НДС20": "НДС 20%", "НДС10": "НДС 10%", "НДС0": "НДС 0%", "БезНДС": "Без НДС"}
DEFAULT_VAT = "НДС22"
DEFAULT_OPERATION = "Товары"
# реквизиты 1С, которые мы пишем; клиент может переименовать любой строкой «Стандартное=Своё»
FIELDS = (
    "НаименованиеПолное",
    "ИНН",
    "КПП",
    "ЮридическоеФизическоеЛицо",
    "Организация_Key",
    "Контрагент_Key",
    "ДоговорКонтрагента_Key",
    "Склад_Key",
    "ВидОперации",
    "НомерВходящегоДокумента",
    "ДатаВходящегоДокумента",
    "Комментарий",
    "СуммаВключаетНДС",
    "ВалютаДокумента_Key",
    "СуммаДокумента",
    "Товары",
    "Номенклатура_Key",
    "Количество",
    "Цена",
    "Сумма",
    "СтавкаНДС",
    "СуммаНДС",
)
# роли для сопоставления «поле документа → поле 1С»; позиции — колонки таблицы в виде «items.name»
ROLES = {
    "counterparty": "Контрагент: наименование",
    "counterparty_inn": "Контрагент: ИНН",
    "counterparty_kpp": "Контрагент: КПП",
    "incoming_number": "Номер входящего документа",
    "incoming_date": "Дата входящего документа",
    "item_name": "Позиция: наименование (номенклатура)",
    "item_qty": "Позиция: количество",
    "item_price": "Позиция: цена",
    "item_amount": "Позиция: сумма",
    "item_vat_amount": "Позиция: сумма НДС",
    "item_amount_with_vat": "Позиция: сумма с НДС (если есть — суммы без НДС, НДС сверху)",
}
ITEM_COLUMNS = {
    "item_name": "name",
    "item_qty": "qty",
    "item_price": "price",
    "item_amount": "amount",
    "item_vat_amount": "vat_amount",
    "item_amount_with_vat": "amount_with_vat",
}
MAX_MISSING_SHOWN = 3
MIN_FALLBACK_NAME = 4  # короче — поиск по вхождению не делаем
FALLBACK_LENGTH_RATIO = 3  # наименование в 1С не длиннее распознанного в 3 раза
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_NAME = re.compile(r"[^\W\d]\w*")  # имя объекта/реквизита 1С: буквы (в т. ч. кириллица), цифры, «_»
_KOPECK = Decimal("0.01")
# больше триллиона рублей/штук в одной строке не бывает: это мусор OCR, а 1С и Decimal на таком ломаются
MAX_AMOUNT = Decimal(10) ** 12
BAD_AMOUNT = "некорректная сумма в документе"
_AUTH_MESSAGE = "1С не приняла логин или пароль (или у пользователя нет прав на OData)"


def _transient(message: str) -> ConnectorError:
    return ConnectorError(message, retryable=True)


def _root(raw: Any) -> str:
    """Адрес публикации с /odata/standard.odata/ на конце — принимаем с ним и без него."""
    url = str(raw or "").strip().rstrip("/")
    if not url:
        raise ConnectorError("не указан адрес публикации 1С")
    if "@" in urlsplit(url).netloc:
        raise ConnectorError(
            "в адресе публикации 1С не должно быть логина и пароля — укажите их в полях «Пользователь 1С» и «Пароль»"
        )
    if not url.endswith(ODATA_SUFFIX):
        url += ODATA_SUFFIX
    return url + "/"


def _literal(value: str) -> str:
    """Строковый литерал OData: одинарная кавычка удваивается."""
    return "'" + value.replace("'", "''") + "'"


def _query(params: dict[str, str]) -> str:
    # пробел — %20, а не «+»: 1С не везде понимает «+» в $filter
    return "&".join(f"{quote(k, safe='$')}={quote(v, safe=chr(39) + '(),')}" for k, v in params.items())


def _detail(resp: httpx.Response) -> str:
    """Текст ошибки 1С из odata.error (v3) или error (v4), очищенный и короткий."""
    try:
        body = resp.json()
    except ValueError:
        return ""
    if not isinstance(body, dict):
        return ""
    err = body.get("odata.error") or body.get("error")
    if not isinstance(err, dict):
        return ""
    msg = err.get("message")
    if isinstance(msg, dict):
        msg = msg.get("value")
    return " ".join(clean(msg).split())[:300]


def _num(value: Any) -> Decimal | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = Decimal(str(value))
        return number if number.is_finite() else None
    text = re.sub(r"\s+", "", clean(value)).replace(",", ".")
    try:
        number = Decimal(text) if text else None
    except ArithmeticError:
        return None
    return number if number is not None and number.is_finite() else None


def _out(value: Decimal) -> int | float:
    return int(value) if value == value.to_integral_value() else float(value)


def _checked(value: Decimal | None) -> Decimal | None:
    if value is not None and abs(value) >= MAX_AMOUNT:
        raise ConnectorError(BAD_AMOUNT)
    return value


def _kopecks(value: Decimal) -> Decimal:
    try:
        result = value.quantize(_KOPECK, rounding=ROUND_HALF_UP)
    except ArithmeticError:  # InvalidOperation: значение не помещается в точность Decimal
        raise ConnectorError(BAD_AMOUNT) from None
    _checked(result)
    return result


def _odata_date(d: date) -> str:
    return f"{d:%Y-%m-%d}T00:00:00"


class _Settings:
    """Настройки получателя, проверенные до первого запроса: ошибка в них — не повод для ретраев."""

    def __init__(self, config: dict[str, Any]):
        kind = str(config.get("document_kind") or "purchase").strip()
        if kind not in DOC_OBJECTS:
            raise ConnectorError("«Вид документа»: выберите purchase (поступление) или sale (реализация)")
        self.kind = kind
        self.document = self._object(config, "document_object", "Объект документа", DOC_OBJECTS[kind])
        self.counterparties = self._object(
            config, "counterparty_object", "Справочник контрагентов", COUNTERPARTY_OBJECT
        )
        self.nomenclature = self._object(config, "nomenclature_object", "Справочник номенклатуры", NOMENCLATURE_OBJECT)
        self.organization = self._guid(config, "organization_key", "Организация (Ref_Key)", required=True)
        self.warehouse = self._guid(config, "warehouse_key", "Склад (Ref_Key)")
        self.contract = self._guid(config, "contract_key", "Договор (Ref_Key)")
        self.currency = self._guid(config, "currency_key", "Валюта (Ref_Key)")
        rate = str(config.get("vat_rate") or DEFAULT_VAT).strip()
        if rate not in VAT_RATES:
            raise ConnectorError(f"«Ставка НДС»: выберите одну из {', '.join(VAT_RATES)}")
        self.vat_rate, self.vat_percent = rate, Decimal(VAT_RATES[rate])
        self.operation = clean(config.get("operation_kind")) or DEFAULT_OPERATION
        self.f = self._fields(str(config.get("field_names") or ""))

    @staticmethod
    def _object(config: dict[str, Any], key: str, label: str, default: str) -> str:
        name = str(config.get(key) or "").strip() or default
        if not _NAME.fullmatch(name):
            raise ConnectorError(f"«{label}»: имя объекта 1С вида Catalog_Контрагенты, без пробелов и знаков")
        return name

    @staticmethod
    def _guid(config: dict[str, Any], key: str, label: str, *, required: bool = False) -> str | None:
        value = str(config.get(key) or "").strip()
        if not value:
            if required:
                raise ConnectorError(f"не указано поле «{label}»")
            return None
        if not _GUID.fullmatch(value):
            raise ConnectorError(f"«{label}»: нужен Ref_Key — GUID вида 1a2b3c4d-…, а не название")
        return value.lower()

    @staticmethod
    def _fields(text: str) -> dict[str, str]:
        names = {name: name for name in FIELDS}
        for line in text.splitlines():
            if not line.strip():
                continue
            std, sep, own = (part.strip() for part in line.partition("="))
            if not sep or std not in names:
                raise ConnectorError(
                    f"«Имена реквизитов»: неизвестный реквизит «{clean(std)[:50]}» — пишите «Стандартное=Своё»"
                )
            if not _NAME.fullmatch(own):
                raise ConnectorError(f"«Имена реквизитов»: недопустимое имя «{clean(own)[:50]}» для {std}")
            names[std] = own
        return names

    def required_objects(self) -> list[str]:
        return [self.counterparties, self.nomenclature, self.document, f"{self.document}_{self.f['Товары']}"]


class _Session:
    def __init__(self, ctx: DestCtx):
        self.http = ctx.http
        self.root = _root(ctx.config.get("base_url"))
        user = str(ctx.secrets.get("username") or "")
        if not user.strip():
            raise ConnectorError("не указан логин пользователя 1С")
        self.auth = httpx.BasicAuth(user, str(ctx.secrets.get("password") or ""))

    async def send(
        self,
        method: str,
        entity: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
        accept: str = "application/json",
    ) -> httpx.Response:
        path = "$metadata" if entity == "$metadata" else quote(entity)
        url = self.root + path + ("?" + _query(params) if params else "")
        try:
            resp = await self.http.request(method, url, json=body, auth=self.auth, headers={"Accept": accept})
        except httpx.HTTPError as exc:
            raise _transient(f"1С не ответила ({type(exc).__name__})") from None
        code = resp.status_code
        if code in (401, 403):
            raise ConnectorError(_AUTH_MESSAGE)
        if code == 404:
            if entity == "$metadata":
                raise ConnectorError(
                    "по адресу нет публикации OData — проверьте адрес и что стандартный интерфейс OData опубликован"
                )
            raise ConnectorError(
                f"в публикации OData нет объекта {entity} — включите его в составе стандартного интерфейса OData"
            )
        detail = _detail(resp)
        suffix = f": {detail}" if detail else ""
        if code == 429 or code >= 500:
            raise _transient(f"1С временно недоступна (код {code}){suffix}")
        if 300 <= code < 400:
            raise ConnectorError(f"1С перенаправила запрос (код {code}) — укажите точный адрес публикации")
        if code >= 400:
            raise ConnectorError(f"1С отклонила запрос к {entity}{suffix or f' (код {code})'}")
        return resp

    async def json(self, method: str, entity: str, *, params: dict[str, str] | None = None, body: Any = None) -> dict:
        resp = await self.send(method, entity, params={**(params or {}), "$format": "json"}, body=body)
        try:
            data = resp.json()
        except ValueError:
            data = None
        if not isinstance(data, dict):
            raise ConnectorError("1С ответила не JSON — проверьте адрес публикации OData")
        return data

    async def find(self, entity: str, flt: str, select: str, top: int | None = None) -> list[dict]:
        params = {"$filter": flt, "$select": select}
        if top:
            params["$top"] = str(top)
        rows = (await self.json("GET", entity, params=params)).get("value")
        if not isinstance(rows, list):
            raise ConnectorError(f"1С вернула неожиданный ответ на поиск в {entity}")
        return [r for r in rows if isinstance(r, dict) and isinstance(r.get("Ref_Key"), str)]

    async def create(self, entity: str, body: dict[str, Any]) -> str:
        created = await self.json("POST", entity, body=body)
        key = created.get("Ref_Key")
        if not isinstance(key, str) or not key:
            raise ConnectorError(f"1С не вернула ключ созданного объекта {entity}")
        return key


_ACTIVE = "DeletionMark eq false"


async def _find_nomenclature(s: _Session, st: _Settings, name: str) -> str | None:
    flt = f"{_ACTIVE} and IsFolder eq false"
    exact = await s.find(st.nomenclature, f"Description eq {_literal(name)} and {flt}", "Ref_Key,Description", 1)
    if exact:
        return exact[0]["Ref_Key"]
    # запасной вариант: наименование в 1С длиннее распознанного — берём, только если совпадение одно и не «чужое»:
    # короткое имя («Мел») найдётся где угодно, а имя в 1С втрое длиннее — уже другая позиция
    if len(name) < MIN_FALLBACK_NAME:
        return None
    similar = await s.find(
        st.nomenclature, f"substringof({_literal(name)}, Description) and {flt}", "Ref_Key,Description", 2
    )
    if len(similar) != 1 or len(str(similar[0].get("Description") or "")) > FALLBACK_LENGTH_RATIO * len(name):
        return None
    return similar[0]["Ref_Key"]


async def _nomenclature(s: _Session, st: _Settings, names: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    missing: list[str] = []
    for name in dict.fromkeys(names):
        key = await _find_nomenclature(s, st, name)
        if key:
            found[name] = key
        else:
            missing.append(name)
    if missing:
        shown = ", ".join(f"«{n[:100]}»" for n in missing[:MAX_MISSING_SHOWN])
        more = f" и ещё {len(missing) - MAX_MISSING_SHOWN}" if len(missing) > MAX_MISSING_SHOWN else ""
        raise ConnectorError(f"в 1С нет номенклатуры: {shown}{more} — заведите её или добавьте сопоставление")
    return found


async def _counterparty(s: _Session, st: _Settings, p: Party) -> str:
    """Контрагент: по ИНН (с подходящим КПП, если их несколько), без ИНН — по точному названию; иначе создаём."""
    f = st.f
    if p.inn:
        rows = await s.find(
            st.counterparties,
            f"{f['ИНН']} eq {_literal(p.inn)} and {_ACTIVE} and IsFolder eq false",
            f"Ref_Key,Description,{f['КПП']}",
        )
        if rows:
            same_kpp = [r for r in rows if p.kpp and str(r.get(f["КПП"]) or "") == p.kpp]
            return (same_kpp or rows)[0]["Ref_Key"]
    else:
        rows = await s.find(
            st.counterparties,
            f"Description eq {_literal(p.name[:100])} and {_ACTIVE} and IsFolder eq false",
            "Ref_Key,Description",
        )
        if rows:
            return rows[0]["Ref_Key"]
    name = p.name or f"ИНН {p.inn}"
    individual = len(p.inn) == 12 or (not p.inn and re.match(r"(?i)ип\s", name) is not None)
    body: dict[str, Any] = {
        "Description": name[:100],
        f["НаименованиеПолное"]: name,
        f["ЮридическоеФизическоеЛицо"]: "ФизическоеЛицо" if individual else "ЮридическоеЛицо",
    }
    if p.inn:
        body[f["ИНН"]] = p.inn
    if p.kpp and len(p.inn) == 10:
        body[f["КПП"]] = p.kpp
    return await s.create(st.counterparties, body)


class _Sources:
    """Откуда брать значения для 1С: умолчания по встроенным схемам, поверх — сопоставление клиента."""

    def __init__(self, ctx: DestCtx, doc: ExportDoc, st: _Settings):
        for role in ctx.mapping.values():
            if str(role).strip() not in ROLES:
                raise ConnectorError(f"поле 1С «{clean(role)[:50]}» не поддерживается; доступны: {', '.join(ROLES)}")
        mapped = {str(role).strip(): str(doc_field).strip() for doc_field, role in ctx.mapping.items()}
        self.mapped = mapped
        side = "supplier" if st.kind == "purchase" else "buyer"
        party_roles = ("counterparty", "counterparty_inn", "counterparty_kpp")
        if any(r in mapped for r in party_roles):  # контрагент сопоставлен — чужие ИНН/КПП не подмешиваем
            self.party = {r: mapped.get(r) for r in party_roles}
        else:
            self.party = dict(zip(party_roles, (side, f"{side}_inn", f"{side}_kpp"), strict=True))
        table = table_field(doc.schema)
        name_src = mapped.get("item_name", "")
        self.table = name_src.split(".", 1)[0] if "." in name_src else (table.name if table else None)
        spec = next((f for f in doc.schema.fields if f.name == self.table and f.type == "table"), None)
        known = {c.name for c in spec.columns} if spec and spec.columns else set()
        self.columns: dict[str, str | None] = {}
        for role, default in ITEM_COLUMNS.items():
            src = mapped.get(role)
            self.columns[role] = src.split(".", 1)[-1] if src else (default if default in known else None)

    def party_of(self, doc: ExportDoc) -> Party:
        get = doc.data.get
        name, inn, kpp = (get(self.party[r]) if self.party[r] else None for r in self.party)
        return party_from(name, inn, kpp)

    def rows(self, doc: ExportDoc) -> list[dict[str, Any]]:
        raw = doc.data.get(self.table) if self.table else None
        return [r for r in raw if isinstance(r, dict)] if isinstance(raw, list) else []

    def cell(self, row: dict[str, Any], role: str) -> Any:
        col = self.columns.get(role)
        return row.get(col) if col else None


class _Line:
    def __init__(self, name: str, qty: Decimal, price: Decimal, amount: Decimal, vat: Decimal | None):
        self.name, self.qty, self.price, self.amount, self.vat = name, qty, price, amount, vat


def _lines(src: _Sources, doc: ExportDoc) -> list[_Line]:
    lines = []
    for i, row in enumerate(src.rows(doc), start=1):
        name = " ".join(clean(src.cell(row, "item_name")).split())
        qty, price, amount = (_checked(_num(src.cell(row, r))) for r in ("item_qty", "item_price", "item_amount"))
        if not name:
            if qty is None and price is None and amount is None:
                continue  # пустая строка таблицы
            raise ConnectorError(f"позиции: строка {i} без наименования — исправьте документ")
        if amount is None and qty is not None and price is not None:
            amount = _kopecks(qty * price)
        if qty is None:
            try:
                qty = (amount / price).quantize(Decimal("0.001")) if amount is not None and price else Decimal(1)
            except ArithmeticError:
                raise ConnectorError(BAD_AMOUNT) from None
            _checked(qty)
        if price is None:
            price = _kopecks(amount / qty) if amount is not None and qty else Decimal(0)
        lines.append(
            _Line(
                name,
                qty,
                price,
                amount if amount is not None else Decimal(0),
                _checked(_num(src.cell(row, "item_vat_amount"))),
            )
        )
    return lines


def _vat_on_top(src: _Sources, doc: ExportDoc, lines: list[_Line]) -> bool:
    """Суммы позиций без НДС (НДС сверху): в таблице есть «сумма с НДС» (УПД) или итог = подытог + НДС (счёт)."""
    if src.columns.get("item_amount_with_vat"):
        return True
    subtotal, total = _num(doc.data.get("subtotal")), _num(doc.data.get("total"))
    if subtotal is None or total is None or total - subtotal <= Decimal("0.005"):
        return False
    return abs(sum((ln.amount for ln in lines), Decimal(0)) - subtotal) <= _KOPECK


def _incoming(src: _Sources, doc: ExportDoc) -> tuple[str, date | None]:
    number = (
        clean(doc.data.get(src.mapped["incoming_number"])) if "incoming_number" in src.mapped else clean(doc.number)
    )
    issued = doc.issued
    if "incoming_date" in src.mapped:
        raw = doc.data.get(src.mapped["incoming_date"])
        try:
            issued = date.fromisoformat(clean(raw)) if raw else None
        except ValueError:
            issued = None
    return number, issued


def _entity_properties(metadata: str, entity: str) -> set[str] | None:
    """Реквизиты типа объекта из $metadata; None — тип не описан (тогда не проверяем)."""
    m = re.search(rf"<EntityType\b[^>]*\bName=\"{re.escape(entity)}\"[^>]*>(.*?)</EntityType>", metadata, re.S)
    return set(re.findall(r"<Property\b[^>]*\bName=\"([^\"]+)\"", m.group(1))) if m else None


def _date_window(day: date) -> str:
    """Дата документа ±1 день: сужает поиск по комментарию (иначе 1С перебирает все документы)."""
    since, until = day - timedelta(days=1), day + timedelta(days=1)
    return f" and Date ge datetime'{since:%Y-%m-%d}T00:00:00' and Date le datetime'{until:%Y-%m-%d}T23:59:59'"


class OneCOData(BaseConnector):
    kind = "onec_odata"
    label = "1С (OData)"
    description = "поступления и контрагенты по ИНН"
    config_fields = [
        FieldDef(
            "base_url",
            "Адрес публикации 1С",
            required=True,
            type="url",
            help="Адрес опубликованной базы, например https://1c.example.ru/base (можно с /odata/standard.odata). "
            "В Конфигураторе: Администрирование → Публикация на веб-сервере, флажок «Публиковать стандартный "
            "интерфейс OData»; состав объектов задаётся обработкой «Настройка автоматического REST-сервиса».",
        ),
        FieldDef(
            "organization_key",
            "Организация (Ref_Key)",
            required=True,
            help="GUID вашей организации из справочника «Организации». Где взять: откройте в браузере "
            "<адрес публикации>/odata/standard.odata/Catalog_Организации?$format=json&$select=Ref_Key,Description "
            "и скопируйте Ref_Key нужной строки (или «Навигационная ссылка» в карточке организации).",
        ),
        FieldDef(
            "document_kind",
            "Вид документа",
            type="select",
            options=["purchase", "sale"],
            option_labels={"purchase": "Поступление (входящие документы)", "sale": "Реализация (исходящие)"},
            help="Поступление товаров и услуг — входящие счета, УПД, накладные (контрагент — поставщик); "
            "Реализация товаров и услуг — исходящие (контрагент — покупатель).",
        ),
        FieldDef("warehouse_key", "Склад (Ref_Key)", help="GUID склада из Catalog_Склады; пусто — заполнит бухгалтер."),
        FieldDef(
            "contract_key",
            "Договор (Ref_Key)",
            help="GUID договора из Catalog_ДоговорыКонтрагентов; обычно пусто.",
            advanced=True,
        ),
        FieldDef(
            "currency_key",
            "Валюта (Ref_Key)",
            help="GUID валюты из Catalog_Валюты; пусто — валюту, как и счета учёта, заполнит бухгалтер "
            "(«Заполнить счета учета» в документе).",
            advanced=True,
        ),
        FieldDef(
            "vat_rate",
            "Ставка НДС",
            type="select",
            options=list(VAT_RATES),
            option_labels=VAT_LABELS,
            help="Ставка для всех позиций; по умолчанию НДС 22% (основная ставка с 2026 года).",
        ),
        FieldDef(
            "operation_kind",
            "Вид операции",
            help="Значение ВидОперации документа; по умолчанию «Товары».",
            advanced=True,
        ),
        FieldDef(
            "document_object",
            "Объект документа",
            help="Пусто — по виду документа (Document_ПоступлениеТоваровУслуг).",
            advanced=True,
        ),
        FieldDef("counterparty_object", "Справочник контрагентов", help="Пусто — Catalog_Контрагенты.", advanced=True),
        FieldDef("nomenclature_object", "Справочник номенклатуры", help="Пусто — Catalog_Номенклатура.", advanced=True),
        FieldDef(
            "field_names",
            "Имена реквизитов",
            type="textarea",
            help="Для нетиповых конфигураций: строки «Стандартное=Своё», например «Комментарий=Примечание». "
            "Умолчания — «Бухгалтерия предприятия 3.0»; в УТ 11 и ERP флаг НДС называется иначе — добавьте "
            f"«СуммаВключаетНДС=ЦенаВключаетНДС». Стандартные: {', '.join(FIELDS)}.",
            advanced=True,
        ),
    ]
    secret_fields = [
        FieldDef("username", "Пользователь 1С", required=True, help="Пользователь базы с правами на чтение и запись."),
        FieldDef("password", "Пароль", help="Пароль этого пользователя (может быть пустым)."),
    ]
    target_fields = [FieldDef(role, label) for role, label in ROLES.items()]

    async def test(self, ctx: DestCtx) -> str | None:
        st = _Settings(ctx.config)
        resp = await _Session(ctx).send("GET", "$metadata", accept="application/xml")
        present = set(re.findall(r"<EntitySet\b[^>]*\bName=\"([^\"]+)\"", resp.text))
        missing = [name for name in st.required_objects() if name not in present]
        if missing:
            raise ConnectorError(
                f"в публикации OData нет объектов: {', '.join(missing)} — включите их в составе стандартного "
                "интерфейса OData"
            )
        # флаг НДС: в БП 3.0 — СуммаВключаетНДС, в УТ 11/ERP — ЦенаВключаетНДС; проверяем, если тип описан
        props = _entity_properties(resp.text, st.document)
        flag = st.f["СуммаВключаетНДС"]
        if props is not None and flag not in props:
            raise ConnectorError(
                f"у {st.document} нет реквизита {flag} — для УТ/ERP добавьте в «Имена реквизитов» строку "
                "«СуммаВключаетНДС=ЦенаВключаетНДС»"
            )
        return "Публикация 1С доступна, объекты найдены"

    async def export(self, ctx: DestCtx, doc: ExportDoc) -> str | None:
        st = _Settings(ctx.config)
        s = _Session(ctx)
        src = _Sources(ctx, doc, st)
        f = st.f
        lines = _lines(src, doc)
        party = src.party_of(doc)
        if not party.inn and not party.name:
            raise ConnectorError(
                "в документе не распознан контрагент (нет ни названия, ни ИНН) — выгрузить в 1С нельзя"
            )

        # без даты документа ставим «сегодня», а повтор может прийти завтра — тогда ищем без окна по дате
        # помеченный на удаление документ — не «уже выгружен»: бухгалтер его удалил, создаём заново
        flt = f"substringof({_literal(marker(doc))}, {f['Комментарий']}) and {_ACTIVE}"
        existing = await s.find(st.document, flt + (_date_window(doc.issued) if doc.issued else ""), "Ref_Key,Number")
        if existing:
            return existing[0]["Ref_Key"]

        goods = await _nomenclature(s, st, [ln.name for ln in lines]) if lines else {}
        counterparty = await _counterparty(s, st, party)

        comment = " · ".join(
            part
            for part in (f"DocFlow {marker(doc)}", doc.cabinet_url, "" if lines else "позиции не распознаны")
            if part
        )
        number, issued = _incoming(src, doc)
        body: dict[str, Any] = {
            "Date": _odata_date(doc.issued or date.today()),
            f["Организация_Key"]: st.organization,
            f["Контрагент_Key"]: counterparty,
            f["ВидОперации"]: st.operation,
            f["Комментарий"]: comment,
        }
        if st.contract:
            body[f["ДоговорКонтрагента_Key"]] = st.contract
        if st.warehouse:
            body[f["Склад_Key"]] = st.warehouse
        if st.currency:
            body[f["ВалютаДокумента_Key"]] = st.currency
        if st.kind == "purchase":
            if number:
                body[f["НомерВходящегоДокумента"]] = number
            if issued:
                body[f["ДатаВходящегоДокумента"]] = _odata_date(issued)
        if lines:
            on_top = _vat_on_top(src, doc, lines)
            rate = st.vat_percent
            rows, total = [], Decimal(0)
            for ln in lines:
                if not rate:
                    vat = Decimal(0)
                elif on_top:
                    vat = ln.vat if ln.vat is not None else _kopecks(ln.amount * rate / 100)
                else:
                    vat = _kopecks(ln.amount * rate / (100 + rate))
                total += ln.amount + (vat if on_top else 0)
                rows.append(
                    {
                        f["Номенклатура_Key"]: goods[ln.name],
                        f["Количество"]: _out(ln.qty),
                        f["Цена"]: _out(ln.price),
                        f["Сумма"]: _out(ln.amount),
                        f["СтавкаНДС"]: st.vat_rate,
                        f["СуммаНДС"]: _out(vat),
                    }
                )
            body[f["СуммаВключаетНДС"]] = not on_top
            body[f["СуммаДокумента"]] = _out(total)
            body[f["Товары"]] = rows
        elif (doc_total := total_of(doc)) is not None:
            body[f["СуммаВключаетНДС"]] = True
            body[f["СуммаДокумента"]] = doc_total
        return await s.create(st.document, body)


REGISTRY["onec_odata"] = OneCOData()

__all__ = ["OneCOData"]
