"""Общее для CRM-коннекторов (Битрикс24, amoCRM): контрагент документа, заголовок, значения полей, ссылки."""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from docflow.connectors.base import ConnectorError, DestCtx, ExportDoc
from docflow.schemas.model import ColumnSpec
from docflow.tabular import clean_text

COUNTERPARTIES = ("supplier", "buyer")
_INN = re.compile(r"\d{10}|\d{12}")
_KPP = re.compile(r"\d{9}")


def clean(value: Any) -> str:
    """Недоверенный текст (OCR) без управляющих символов и по краям пробелов."""
    return clean_text(str(value)).strip() if value is not None else ""


def int_setting(config: dict[str, Any], name: str, label: str, *, allow_zero: bool = False) -> int | None:
    raw = config.get(name)
    if raw in (None, ""):
        return None
    try:
        value = int(str(raw).strip())
    except ValueError:
        raise ConnectorError(f"«{label}» должно быть целым числом") from None
    if value < 0 or (value == 0 and not allow_zero):
        raise ConnectorError(f"«{label}» должно быть положительным числом")
    return value


def counterparty_side(config: dict[str, Any]) -> str:
    side = str(config.get("counterparty") or "supplier").strip()
    if side not in COUNTERPARTIES:
        raise ConnectorError("контрагент: выберите «supplier» (поставщик) или «buyer» (покупатель)")
    return side


@dataclass(frozen=True)
class Party:
    name: str
    inn: str
    kpp: str


def party(doc: ExportDoc, side: str) -> Party:
    """Контрагент по умолчанию: supplier/supplier_inn/supplier_kpp (или buyer…). Кривой ИНН/КПП отбрасываем."""
    data = doc.data
    return party_from(data.get(side), data.get(f"{side}_inn"), data.get(f"{side}_kpp"))


def party_from(name: Any, inn: Any, kpp: Any) -> Party:
    """Контрагент из сырых значений документа: текст очищен, кривой ИНН/КПП отброшен."""
    inn = re.sub(r"\s+", "", clean(inn))
    kpp = re.sub(r"\s+", "", clean(kpp))
    return Party(
        name=clean(name)[:255],
        inn=inn if _INN.fullmatch(inn) else "",
        kpp=kpp if _KPP.fullmatch(kpp) else "",
    )


def deal_title(doc: ExportDoc, max_len: int = 250) -> str:
    parts = [clean(doc.title) or "Документ"]
    if doc.number:
        parts.append(f"№ {clean(doc.number)}")
    if doc.issued:
        parts.append(f"от {doc.issued:%d.%m.%Y}")
    return " ".join(parts)[:max_len]


def total_of(doc: ExportDoc) -> float | None:
    value = doc.data.get("total")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value) if float(value).is_integer() else float(value)


def links_text(doc: ExportDoc) -> list[str]:
    lines = []
    if doc.digital_pdf_url:
        lines.append(f"Цифровая версия (PDF): {doc.digital_pdf_url}")
    if doc.scan_pdf_url:
        lines.append(f"Скан (PDF): {doc.scan_pdf_url}")
    if doc.cabinet_url:
        lines.append(f"Кабинет DocFlow: {doc.cabinet_url}")
    return lines


def marker(doc: ExportDoc) -> str:
    return f"docflow:{doc.id}"


def mapped_values(ctx: DestCtx, doc: ExportDoc, *, native: bool = False) -> list[tuple[str, Any]]:
    """Поля по маппингу: (код поля CRM, значение). Таблицы и пустые значения пропускаем.

    native: типы CRM — дата Unix-временем (полночь UTC), булево как true/false (amoCRM); иначе даты ISO-строкой."""
    flat: dict[str, ColumnSpec] = {f.name: f for f in doc.schema.fields if f.type != "table"}
    out: list[tuple[str, Any]] = []
    for name, code in ctx.mapping.items():
        spec = flat.get(name)
        code = str(code).strip()
        if spec is None or not code:
            continue
        value = field_value(spec, doc.data.get(name), native=native)
        if value is not None:
            out.append((code, value))
    return out


def field_value(spec: ColumnSpec, value: Any, *, native: bool = False) -> Any:
    if value is None or (isinstance(value, (str, list, dict)) and not value):
        return None
    if isinstance(value, str):
        text = clean(value)
        if not text:
            return None
        if spec.type == "date":
            try:
                d = date.fromisoformat(text)
                if native:
                    return int(datetime(d.year, d.month, d.day, tzinfo=UTC).timestamp())
                return d.isoformat()
            except ValueError:
                return text
        return text
    if isinstance(value, bool):
        return value if native else ("да" if value else "нет")
    if isinstance(value, (int, float)):
        return int(value) if float(value).is_integer() else float(value)
    return clean(value)
