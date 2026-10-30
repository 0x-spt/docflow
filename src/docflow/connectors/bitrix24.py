"""Битрикс24: сделка с компанией и ссылками на PDF через входящий вебхук.

Доступ: клиент создаёт входящий вебхук (права CRM) и вставляет его адрес — он содержит ключ, поэтому хранится как секрет
и не попадает ни в ошибки, ни в логи. Идемпотентность: сделка помечена ORIGINATOR_ID=docflow и ORIGIN_ID=<id документа>;
повтор после сбоя находит её и дописывает только недостающий комментарий.
"""

import re
from typing import Any

import httpx

from docflow.connectors.base import (
    REGISTRY,
    BaseConnector,
    ConnectorError,
    DestCtx,
    ExportDoc,
    FieldDef,
    RemoteField,
)
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

ORIGINATOR = "docflow"
COMPANY_TYPE = 4  # ENTITY_TYPE_ID компании в реквизитах
PRESET_ORG, PRESET_IP = 1, 2  # шаблоны реквизитов Битрикс24: «Организация» и «ИП»
_AUTH_ERRORS = {"expired_token", "invalid_token", "no_auth_found", "insufficient_scope", "invalid_credentials"}
_USER_FIELD = re.compile(r"UF_[A-Z0-9_]+")
USER_FIELD_PREFIX = "UF_CRM_"


def _transient(message: str) -> ConnectorError:
    return ConnectorError(message, retryable=True)


class _Session:
    def __init__(self, ctx: DestCtx):
        self.http = ctx.http
        hook = str(ctx.secrets.get("webhook_url") or "").strip()
        if not hook:
            raise ConnectorError("не указан адрес входящего вебхука Битрикс24")
        self.hook = hook if hook.endswith("/") else hook + "/"

    async def call(self, method: str, payload: dict[str, Any] | None = None) -> Any:
        try:
            resp = await self.http.post(f"{self.hook}{method}.json", json=payload or {})
        except httpx.HTTPError as exc:
            raise _transient(f"Битрикс24 не ответил ({type(exc).__name__})") from None
        code = resp.status_code
        error, description = "", ""
        try:
            body = resp.json()
        except ValueError:
            body = None
        if isinstance(body, dict):
            error = str(body.get("error") or "")
            description = clean(body.get("error_description"))[:200]
        if error.upper() == "QUERY_LIMIT_EXCEEDED" or code == 429 or code >= 500:
            raise _transient(f"Битрикс24 временно недоступен или ограничил запросы (код {code})")
        if error.lower() in _AUTH_ERRORS or code in (401, 403):
            raise ConnectorError(f"вебхук Битрикс24 не принят: {description or error or f'код {code}'}")
        if "METHOD_NOT_FOUND" in error.upper():
            raise ConnectorError("метод не найден — проверьте права вебхука")
        if code == 404:
            raise ConnectorError("вебхук Битрикс24 не принят: адрес не найден, проверьте ссылку вебхука")
        if error or code >= 400 or not isinstance(body, dict) or "result" not in body:
            detail = description or error or f"код {code}"
            raise ConnectorError(f"Битрикс24 отклонил запрос {method}: {detail}")
        return body["result"]

    async def first_id(self, method: str, flt: dict[str, Any], key: str) -> str | None:
        rows = await self.call(method, {"filter": flt, "select": [key]})
        if isinstance(rows, list) and rows and isinstance(rows[0], dict) and rows[0].get(key) not in (None, ""):
            return str(rows[0][key])
        return None


async def _company(s: _Session, p: Any) -> str | None:
    """Компания контрагента: по ИНН в реквизитах, затем по названию, иначе создаём (и реквизит с ИНН/КПП)."""
    if p.inn:
        found = await s.first_id("crm.requisite.list", {"=RQ_INN": p.inn, "ENTITY_TYPE_ID": COMPANY_TYPE}, "ENTITY_ID")
        if found:
            return found
    if not p.name:
        return None  # без названия компанию не заводим: ИНН без имени в CRM бесполезен
    company_id: Any = await s.first_id("crm.company.list", {"=TITLE": p.name}, "ID")
    if company_id:
        # найдена по названию: реквизит мог не записаться в прошлой попытке (company.add прошёл, requisite.add нет)
        if p.inn and not await _has_requisite(s, company_id, p.inn):
            await _add_requisite(s, company_id, p)
        return str(company_id)
    company_id = await s.call("crm.company.add", {"fields": {"TITLE": p.name}})
    if p.inn:
        await _add_requisite(s, company_id, p)
    return str(company_id)


async def _has_requisite(s: _Session, company_id: Any, inn: str) -> bool:
    rows = await s.call(
        "crm.requisite.list",
        {"filter": {"ENTITY_ID": int(company_id), "ENTITY_TYPE_ID": COMPANY_TYPE}, "select": ["ID", "RQ_INN"]},
    )
    return isinstance(rows, list) and any(isinstance(r, dict) and str(r.get("RQ_INN") or "") == inn for r in rows)


async def _add_requisite(s: _Session, company_id: Any, p: Any) -> None:
    fields: dict[str, Any] = {
        "ENTITY_TYPE_ID": COMPANY_TYPE,
        "ENTITY_ID": int(company_id),
        "PRESET_ID": PRESET_IP if len(p.inn) == 12 else PRESET_ORG,
        "NAME": p.name,
        "RQ_INN": p.inn,
    }
    if p.kpp and len(p.inn) == 10:
        fields["RQ_KPP"] = p.kpp
    await s.call("crm.requisite.add", {"fields": fields})


def _comment(doc: ExportDoc) -> str:
    return "\n".join([*links_text(doc), marker(doc)])


async def _ensure_comment(s: _Session, deal_id: str, doc: ExportDoc) -> None:
    existing = await s.call(
        "crm.timeline.comment.list",
        {"filter": {"ENTITY_ID": int(deal_id), "ENTITY_TYPE": "deal"}, "select": ["ID", "COMMENT"]},
    )
    if isinstance(existing, list) and any(
        marker(doc) in str(c.get("COMMENT", "")) for c in existing if isinstance(c, dict)
    ):
        return
    await s.call(
        "crm.timeline.comment.add",
        {"fields": {"ENTITY_ID": int(deal_id), "ENTITY_TYPE": "deal", "COMMENT": _comment(doc)}},
    )


class Bitrix24(BaseConnector):
    kind = "bitrix24"
    label = "Битрикс24"
    description = "сделка и компания по ИНН"
    config_fields = [
        FieldDef(
            "assigned_by_id", "Ответственный (ID)", type="number", help="ID сотрудника; по умолчанию владелец вебхука."
        ),
        FieldDef("category_id", "Воронка (ID)", type="number", help="Пусто — воронка по умолчанию."),
        FieldDef("stage_id", "Стадия", help="Код стадии, например NEW или C2:NEW; пусто — первая стадия воронки."),
        FieldDef(
            "counterparty",
            "Контрагент",
            type="select",
            options=["supplier", "buyer"],
            option_labels={"supplier": "Поставщик (входящие счета)", "buyer": "Покупатель (исходящие)"},
            help="Чья компания попадает в сделку: поставщика (входящие счета) или покупателя.",
        ),
    ]
    secret_fields = [
        FieldDef(
            "webhook_url",
            "Входящий вебхук",
            required=True,
            type="url",
            help="Адрес вида https://портал.bitrix24.ru/rest/1/ключ/ с правами на CRM.",
        ),
    ]
    target_fields: list[FieldDef] = []

    async def test(self, ctx: DestCtx) -> str | None:
        profile = await _Session(ctx).call("profile")
        name = " ".join(
            clean(profile.get(k)) for k in ("NAME", "LAST_NAME") if isinstance(profile, dict) and profile.get(k)
        )
        return f"Портал доступен, пользователь {name}" if name else "Портал доступен"

    async def remote_fields(self, ctx: DestCtx) -> list[RemoteField]:
        """Пользовательские поля сделки (UF_CRM_…): только их можно сопоставлять при выгрузке."""
        fields = await _Session(ctx).call("crm.deal.fields")
        if not isinstance(fields, dict):
            return []
        out = []
        for code, meta in fields.items():
            if not (isinstance(code, str) and code.startswith(USER_FIELD_PREFIX) and _USER_FIELD.fullmatch(code)):
                continue
            meta = meta if isinstance(meta, dict) else {}
            title = next((clean(meta.get(k)) for k in ("listLabel", "formLabel", "title") if clean(meta.get(k))), "")
            out.append(RemoteField(code, title or code))
        return out

    async def export(self, ctx: DestCtx, doc: ExportDoc) -> str | None:
        s = _Session(ctx)
        assigned = int_setting(ctx.config, "assigned_by_id", "Ответственный (ID)")
        category = int_setting(ctx.config, "category_id", "Воронка (ID)", allow_zero=True)
        stage = clean(ctx.config.get("stage_id"))
        side = counterparty_side(ctx.config)
        user_fields = mapped_values(ctx, doc)
        for code, _ in user_fields:
            if not _USER_FIELD.fullmatch(code):
                raise ConnectorError(
                    f"поле Битрикс24 «{code}» не подходит: сопоставлять можно только пользовательские (UF_CRM_…)"
                )

        existing = await s.first_id("crm.deal.list", {"=ORIGINATOR_ID": ORIGINATOR, "=ORIGIN_ID": str(doc.id)}, "ID")
        if existing:
            await _ensure_comment(s, existing, doc)
            return existing

        company_id = await _company(s, party(doc, side))
        fields: dict[str, Any] = {
            "TITLE": deal_title(doc),
            "CURRENCY_ID": "RUB",
            "ORIGINATOR_ID": ORIGINATOR,
            "ORIGIN_ID": str(doc.id),
            "COMMENTS": "\n".join(links_text(doc)),
        }
        if (total := total_of(doc)) is not None:
            fields["OPPORTUNITY"] = total
        if company_id:
            fields["COMPANY_ID"] = int(company_id)
        if assigned:
            fields["ASSIGNED_BY_ID"] = assigned
        if category is not None:
            fields["CATEGORY_ID"] = category
        if stage:
            fields["STAGE_ID"] = stage
        fields.update(user_fields)

        deal_id = str(await s.call("crm.deal.add", {"fields": fields, "params": {"REGISTER_SONET_EVENT": "N"}}))
        await _ensure_comment(s, deal_id, doc)
        return deal_id


REGISTRY["bitrix24"] = Bitrix24()

__all__ = ["Bitrix24"]
