"""amoCRM (и Kommo): сделка с компанией одним запросом и примечанием со ссылками на PDF.

Доступ: долгосрочный токен интеграции. Идемпотентность: в названии сделки стоит метка [docflow:<12 символов id>],
повтор ищет сделку по ней (полнотекстовый поиск amoCRM) и проверяет метку точно; полный id лежит в примечании,
поэтому после сбоя между созданием сделки и примечанием дописывается только примечание.
"""

import re
from typing import Any

import httpx

from docflow.connectors.base import REGISTRY, BaseConnector, ConnectorError, DestCtx, ExportDoc, FieldDef
from docflow.connectors.crm_common import (
    clean,
    counterparty_side,
    deal_title,
    int_setting,
    links_text,
    mapped_values,
    marker,
    party,
    total_of,
)

DOMAINS = ("amocrm.ru", "amocrm.com", "kommo.com")
TAG_NAME = "DocFlow"
_LABEL = re.compile(r"[a-z0-9][a-z0-9-]*")


def _transient(message: str) -> ConnectorError:
    return ConnectorError(message, retryable=True)


def api_base(raw: Any) -> str:
    """Адрес API по поддомену: «company», «company.amocrm.ru» или ссылка на аккаунт (в т.ч. kommo.com)."""
    text = str(raw or "").strip().lower()
    text = re.sub(r"^https?://", "", text).split("/")[0]
    if not text:
        raise ConnectorError("укажите поддомен amoCRM, например company или company.amocrm.ru")
    sub, domain = text, DOMAINS[0]
    if "." in text:
        sub, _, domain = text.partition(".")
    if domain not in DOMAINS or not _LABEL.fullmatch(sub):
        raise ConnectorError("поддомен amoCRM указан неверно: ожидается company или company.amocrm.ru (или .kommo.com)")
    return f"https://{sub}.{domain}/api/v4"


def tag_of(doc: ExportDoc) -> str:
    """Метка в конце названия сделки: простой буквенно-цифровой токен (поиск amoCRM разбивает по знакам препинания).

    Остаточный риск: индекс поиска amoCRM обновляется асинхронно, поэтому повтор через секунды после потерянного
    ответа /leads/complex может создать дубль; задержка повтора движка (от 10 с) это смягчает."""
    return f"docflow{doc.id.hex[:12]}"


class _Session:
    def __init__(self, ctx: DestCtx):
        self.http = ctx.http
        self.base = api_base(ctx.config.get("subdomain"))
        token = str(ctx.secrets.get("access_token") or "").strip()
        if not token:
            raise ConnectorError("не указан долгосрочный токен amoCRM")
        self.headers = {"Authorization": f"Bearer {token}"}

    async def call(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            resp = await self.http.request(method, self.base + path, headers=self.headers, **kwargs)
        except httpx.HTTPError as exc:
            raise _transient(f"amoCRM не ответила ({type(exc).__name__})") from None
        code = resp.status_code
        if code == 401:
            raise ConnectorError("токен amoCRM не принят — выпустите новый долгосрочный токен")
        if code == 402:
            raise ConnectorError("аккаунт amoCRM не оплачен")
        if code == 403:
            raise ConnectorError("amoCRM отказала в доступе: у интеграции нет прав на сделки и компании")
        if code == 429 or code == 408 or code >= 500:
            raise _transient(f"amoCRM временно недоступна (код {code})")
        if 300 <= code < 400:
            raise ConnectorError("неверный адрес аккаунта amoCRM (перенаправление)")
        if code in (204, 304) or (code < 300 and not resp.content):
            return None
        if code >= 400:
            raise ConnectorError(f"amoCRM отклонила запрос (код {code}): {_problem(resp)}")
        try:
            return resp.json()
        except ValueError:
            raise _transient("amoCRM вернула непонятный ответ") from None

    async def embedded(self, path: str, key: str, **params: Any) -> list[dict[str, Any]]:
        data = await self.call("GET", path, params=params)
        items = data.get("_embedded", {}).get(key) if isinstance(data, dict) else None
        return [i for i in items or [] if isinstance(i, dict)]


def _problem(resp: httpx.Response) -> str:
    try:
        body = resp.json()
        parts = [clean(body.get("title")), clean(body.get("detail"))]
        errors = body.get("validation-errors")
        if errors:
            parts.append(clean(errors)[:300])
        return "; ".join(p for p in parts if p)[:500] or "без пояснения"
    except Exception:
        return "без пояснения"


def _has_value(company: dict[str, Any], field_id: int, value: str) -> bool:
    for cf in company.get("custom_fields_values") or []:
        if isinstance(cf, dict) and cf.get("field_id") == field_id:
            return any(
                isinstance(v, dict) and clean(v.get("value")).replace(" ", "") == value for v in cf.get("values") or []
            )
    return False


async def _company_ref(s: _Session, p: Any, inn_field: int | None) -> dict[str, Any] | None:
    """Ссылка на компанию для сделки: найденная по ИНН (поле компании) или точному названию, иначе новая."""
    if not p.name and not p.inn:
        return None
    if p.inn and inn_field:
        for c in await s.embedded("/companies", "companies", query=p.inn):
            if _has_value(c, inn_field, p.inn):
                return {"id": c["id"]}
    if p.name:
        for c in await s.embedded("/companies", "companies", query=p.name):
            if clean(c.get("name")).casefold() == p.name.casefold():
                return {"id": c["id"]}
        new: dict[str, Any] = {"name": p.name}
        if p.inn and inn_field:
            new["custom_fields_values"] = [{"field_id": inn_field, "values": [{"value": p.inn}]}]
        return new
    return None  # ИНН без названия: компанию без имени не заводим


def _note_text(doc: ExportDoc) -> str:
    return "\n".join([*links_text(doc), marker(doc)])


async def _ensure_note(s: _Session, lead_id: int, doc: ExportDoc) -> None:
    for note in await s.embedded(f"/leads/{lead_id}/notes", "notes", limit=250):
        if marker(doc) in str((note.get("params") or {}).get("text", "")):
            return
    await s.call("POST", f"/leads/{lead_id}/notes", json=[{"note_type": "common", "params": {"text": _note_text(doc)}}])


def _field_id(code: str) -> int:
    if not code.isdigit():
        raise ConnectorError(f"поле amoCRM «{code}» не подходит: укажите числовой ID поля сделки")
    return int(code)


class AmoCrm(BaseConnector):
    kind = "amocrm"
    label = "amoCRM"
    config_fields = [
        FieldDef(
            "subdomain",
            "Поддомен",
            required=True,
            help="Например company или company.amocrm.ru (kommo.com тоже подойдёт).",
        ),
        FieldDef("pipeline_id", "Воронка (ID)", type="number", help="Пусто — воронка по умолчанию."),
        FieldDef("status_id", "Этап (ID)", type="number", help="Пусто — первый этап воронки."),
        FieldDef("responsible_user_id", "Ответственный (ID)", type="number", help="Пусто — владелец токена."),
        FieldDef(
            "inn_field_id",
            "Поле ИНН у компании (ID)",
            type="number",
            help="Числовой ID пользовательского поля компании для ИНН; без него компании ищутся по названию.",
        ),
        FieldDef(
            "counterparty",
            "Контрагент",
            type="select",
            options=["supplier", "buyer"],
            help="Чья компания попадает в сделку: поставщика (входящие счета) или покупателя.",
        ),
    ]
    secret_fields = [
        FieldDef(
            "access_token",
            "Долгосрочный токен",
            required=True,
            help="Токен из раздела «Ключи и доступы» интеграции amoCRM.",
        ),
    ]
    target_fields: list[FieldDef] = []

    async def test(self, ctx: DestCtx) -> str | None:
        account = await _Session(ctx).call("GET", "/account")
        name = clean(account.get("name")) if isinstance(account, dict) else ""
        return f"Аккаунт «{name}» доступен" if name else "Аккаунт доступен"

    async def export(self, ctx: DestCtx, doc: ExportDoc) -> str | None:
        s = _Session(ctx)
        pipeline = int_setting(ctx.config, "pipeline_id", "Воронка (ID)")
        status = int_setting(ctx.config, "status_id", "Этап (ID)")
        responsible = int_setting(ctx.config, "responsible_user_id", "Ответственный (ID)")
        inn_field = int_setting(ctx.config, "inn_field_id", "Поле ИНН у компании (ID)")
        side = counterparty_side(ctx.config)
        custom = [
            {"field_id": _field_id(code), "values": [{"value": v}]} for code, v in mapped_values(ctx, doc, native=True)
        ]

        tag = tag_of(doc)
        for lead in await s.embedded("/leads", "leads", query=tag, limit=50):
            # endswith: текст OCR внутри названия не подделает метку
            if clean(lead.get("name")).endswith(tag):
                await _ensure_note(s, int(lead["id"]), doc)
                return str(lead["id"])

        lead: dict[str, Any] = {"name": f"{deal_title(doc, 255 - len(tag) - 1)} {tag}"}
        if (total := total_of(doc)) is not None:
            lead["price"] = int(total + 0.5)  # amoCRM хранит бюджет целым числом
        for key, value in (("pipeline_id", pipeline), ("status_id", status), ("responsible_user_id", responsible)):
            if value:
                lead[key] = value
        if custom:
            lead["custom_fields_values"] = custom
        embedded: dict[str, Any] = {"tags": [{"name": TAG_NAME}]}
        if company := await _company_ref(s, party(doc, side), inn_field):
            embedded["companies"] = [company]
        lead["_embedded"] = embedded

        created = await s.call("POST", "/leads/complex", json=[lead])
        try:
            lead_id = int(created[0]["id"])
        except (TypeError, KeyError, IndexError, ValueError):
            raise _transient("amoCRM не сообщила ID созданной сделки") from None
        await _ensure_note(s, lead_id, doc)
        return str(lead_id)


REGISTRY["amocrm"] = AmoCrm()

__all__ = ["AmoCrm", "api_base"]
