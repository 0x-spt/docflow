"""Сценарии демо-тенанта: вымышленные компании, документы, заготовленные ответы модели и правила.

Ответы прогоняются через настоящий конвейер (normalize → справочники → проверки → правила); `expect` —
статус, который он должен дать. Все названия и реквизиты вымышлены.
"""
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Literal

from docflow.demo.requisites import DEMO_BANK, make_account, make_inn, make_kpp
from docflow.extraction.base import Extraction, ExtractionError
from docflow.schemas.model import DocSchema

SEED = 20261029
DEMO_TENANT_NAME = "Демо — ООО «Северный Путь»"
APPROVAL_LIMIT = 500_000
VAT_RATE = 0.2

@dataclass(frozen=True)
class Company:
    name: str
    inn: str
    kpp: str | None
    address: str


@dataclass(frozen=True)
class _Supplier:
    company: Company
    account: str
    aliases: tuple[str, ...]


def _company(rng: random.Random, name: str, region: str, city: str, street: str, *, person: bool = False) -> Company:
    inn = make_inn(rng, region=region, person=person)
    return Company(name, inn, None if person else make_kpp(inn, rng), f"{city}, {street}")


_EKB = "г. Екатеринбург"
_SVERDL = "Свердловская обл., "


# реквизиты — из своего генератора: от today не зависят
_RNG = random.Random(SEED)
BUYER = _company(_RNG, "ООО «Северный Путь»", "66", _EKB, "ул. Полярная, д. 14, оф. 3")

# покупатель в Екатеринбурге: поставщики в основном из Свердловской области; издалека везут только
# металл (Челябинск) и лес (Архангельская обл.) — бетон, разгрузка, техника и монтаж всегда местные
_SUPPLIER_SPECS = (
    ("ООО «СтройКомплект-Север»", "66", _EKB, "Промышленный пр., д. 8", False,
     ("СтройКомплект-Север", "СтройКомплект Север")),
    ("ООО «Северная Кровля»", "66", _SVERDL + "г. Берёзовский", "ул. Кровельщиков, д. 21", False,
     ("Северная Кровля",)),
    ("ООО «Металлобаза Полярная»", "74", "г. Челябинск", "ш. Складское, д. 3, стр. 2", False,
     ("Металлобаза Полярная",)),
    ("ООО «Бетонный узел Заречье»", "66", _SVERDL + "г. Заречный", "ул. Карьерная, д. 1", False,
     ("БУ Заречье", "Бетонный узел Заречье")),
    ("ООО «Лесоторг Онежский»", "29", "Архангельская обл., г. Онега", "наб. Лесная, д. 5", False,
     ("Лесоторг Онежский",)),
    ("ООО «Электроснаб Плюс»", "66", _EKB, "ул. Энергетиков, д. 40", False, ("Электроснаб Плюс", "Электроснаб+")),
    ("ИП Карпенко Олег Витальевич", "66", _EKB, "ул. Садовая, д. 9, кв. 17", True,
     ("ИП Карпенко О. В.", "Карпенко О. В.")),
    ("ИП Савельева Марина Андреевна", "66", _SVERDL + "г. Верхняя Пышма", "пер. Тихий, д. 2", True,
     ("ИП Савельева М. А.", "Савельева М. А.")),
)  # fmt: skip
_SUPPLIERS = [
    _Supplier(_company(_RNG, name, region, city, street, person=person), make_account(DEMO_BANK.bik, _RNG), aliases)
    for name, region, city, street, person, aliases in _SUPPLIER_SPECS
]
STROYKOMPLEKT, KROVLYA, METALL, BETON, LES, ELEKTRO, KARPENKO, SAVELIEVA = _SUPPLIERS
# подрядчик акта, которого нет в справочнике поставщиков
_OUTSIDER = _company(_RNG, "ООО «Гранит-Монолит»", "66", _SVERDL + "г. Арамиль", "ул. Каменная, д. 12")

SUPPLIERS_DICT: dict = {
    "title": "Поставщики",
    "entries": [{"value": s.company.name, "aliases": list(s.aliases)} for s in _SUPPLIERS],
}

# позиции: наименование, ед. изм., цена за единицу без НДС
_ITEMS = {
    "cement": ("Цемент М500 Д0, мешок 50 кг", "меш", 520),
    "sand": ("Песок строительный мытый", "м³", 950),
    "gravel": ("Щебень гранитный фр. 20–40", "м³", 2350),
    "gasblock": ("Газобетонный блок D500 600×300×200", "м³", 6450),
    "brick": ("Кирпич облицовочный М150", "шт", 34),
    "profsheet": ("Профлист С21-1000, 0,5 мм, оцинкованный", "м²", 610),
    "screws": ("Саморез кровельный 4,8×35, уп. 250 шт", "уп", 780),
    "membrane": ("Мембрана гидроизоляционная, рулон 75 м²", "рул", 3200),
    "wool": ("Утеплитель минвата 100 мм, уп. 0,3 м³", "уп", 1450),
    "rebar": ("Арматура А500С ⌀12 мм", "т", 68500),
    "pipe": ("Труба профильная 60×40×2 мм", "м", 410),
    "steel": ("Лист горячекатаный 3 мм", "т", 72400),
    "concrete": ("Бетон В25 (М350) П4", "м³", 6900),
    "pump": ("Услуги автобетононасоса", "час", 5500),
    "beam": ("Брус 150×150×6000, сосна, 1 сорт", "м³", 18900),
    "board": ("Доска обрезная 50×150×6000, сосна", "м³", 17200),
    "cable": ("Кабель ВВГнг-LS 3×2,5", "м", 96),
    "breaker": ("Автомат дифференциальный 16 А", "шт", 1850),
    "delivery": ("Доставка манипулятором по городу", "рейс", 7500),
    "excavator": ("Аренда мини-экскаватора с оператором", "час", 3200),
    "roof_work": ("Монтаж кровли из профлиста", "м²", 450),
    "steel_work": ("Монтаж металлоконструкций", "т", 24000),
    "electro_work": ("Электромонтажные работы", "точка", 850),
    "cleanup": ("Уборка строительного мусора", "м³", 900),
    "waste": ("Вывоз мусора, контейнер 8 м³", "рейс", 9800),
    "unloading": ("Разгрузка материалов", "час", 1200),
    "base_work": ("Устройство бетонного основания", "м²", 1100),
}

ACT_SCHEMA: dict = {
    "slug": "act_works",
    "version": 1,
    "title": "Акт выполненных работ",
    "description": "Акт выполненных работ (оказанных услуг) от подрядчика заказчику, с таблицей работ.",
    "fields": [
        {"name": "number", "title": "Номер", "type": "string", "required": True,
         "description": "Номер акта строкой, как написан, без слова «Акт №»."},
        {"name": "issued", "title": "Дата", "type": "date", "required": True,
         "description": "Дата акта (после «от»). null, если даты нет.", "rules": [{"kind": "date_not_future"}]},
        {"name": "contractor", "title": "Исполнитель", "type": "string",
         "description": "Исполнитель (подрядчик): наименование как написано, с формой («ООО …», «ИП …»).",
         "rules": [{"kind": "dictionary", "params": {"name": "suppliers"}}]},
        {"name": "contractor_inn", "title": "ИНН исполнителя", "type": "string",
         "description": "ИНН исполнителя: 10 или 12 цифр без пробелов.", "rules": [{"kind": "inn"}]},
        {"name": "customer", "title": "Заказчик", "type": "string",
         "description": "Заказчик: наименование как написано."},
        {"name": "items", "title": "Работы", "type": "table", "required": True,
         "description": "Все строки таблицы работ по порядку, без строки «Итого».",
         "columns": [
             {"name": "name", "title": "Наименование", "type": "string",
              "description": "Наименование работы целиком, как написано."},
             {"name": "qty", "title": "Кол-во", "type": "number", "description": "Количество (число)."},
             {"name": "unit", "title": "Ед. изм.", "type": "string", "description": "Единица измерения."},
             {"name": "price", "title": "Цена", "type": "number", "description": "Цена за единицу в рублях."},
             {"name": "amount", "title": "Сумма", "type": "number",
              "description": "Сумма по строке, как напечатана."},
         ]},
        {"name": "total", "title": "Итого", "type": "number", "required": True,
         "description": "Итоговая сумма акта, как напечатана."},
    ],
    "rules": [
        {"kind": "table_sum_equals", "params": {"table": "items", "column": "amount", "total": "total"}},
        {"kind": "table_row_product", "params": {"table": "items", "qty": "qty", "price": "price", "amount": "amount"}},
    ],
}  # fmt: skip

Expect = Literal["ok", "needs_review", "failed"]
Review = Literal["approve", "approve_edit", "reject"]


@dataclass
class Scenario:
    key: str
    schema: str
    answer: dict | None
    days_ago: int
    hour: int
    expect: Expect
    review: Review | None = None
    review_note: str | None = None
    # поля верхнего уровня, исправленные оператором: одобряется {**answer, **fix}
    fix: dict | None = None
    pages: int = 1


def _money(x: float) -> float | int:
    x = round(x, 2)
    return int(x) if x == int(x) else x


def _rows(items: list[tuple[str, float]], *, vat: bool = False) -> list[dict]:
    rows = []
    for code, qty in items:
        name, unit, price = _ITEMS[code]
        row = {"name": name, "qty": qty, "unit": unit, "price": price, "amount": _money(qty * price)}
        if vat:
            row["vat_amount"] = _money(row["amount"] * VAT_RATE)
            row["amount_with_vat"] = _money(row["amount"] + row["vat_amount"])
        rows.append(row)
    return rows


def _invoice(s: _Supplier, number: str, issued: date, items: list[tuple[str, float]]) -> dict:
    rows = _rows(items)
    subtotal = _money(sum(r["amount"] for r in rows))
    vat = _money(subtotal * VAT_RATE)
    return {
        "number": number,
        "issued": issued.isoformat(),
        "supplier": s.company.name,
        "supplier_inn": s.company.inn,
        "supplier_kpp": s.company.kpp,
        "buyer": BUYER.name,
        "buyer_inn": BUYER.inn,
        "bik": DEMO_BANK.bik,
        "account": s.account,
        "corr_account": DEMO_BANK.corr,
        "items": rows,
        "subtotal": subtotal,
        "vat": vat,
        "total": _money(subtotal + vat),
    }


def _upd(s: _Supplier, number: str, issued: date, items: list[tuple[str, float]], *, with_vat: bool = True) -> dict:
    rows = _rows(items, vat=with_vat)
    if not with_vat:  # статус 2, без НДС: стоимость с налогом = без налога
        for r in rows:
            r["vat_amount"], r["amount_with_vat"] = None, r["amount"]
    return {
        "number": number,
        "issued": issued.isoformat(),
        "status": "1" if with_vat else "2",
        "supplier": s.company.name,
        "supplier_inn": s.company.inn,
        "supplier_kpp": s.company.kpp,
        "shipper": "он же",
        "consignee": f"{BUYER.name}, {BUYER.address}",
        "buyer": BUYER.name,
        "buyer_inn": BUYER.inn,
        "buyer_kpp": BUYER.kpp,
        "bik": DEMO_BANK.bik,
        "account": DEMO_BANK.corr,  # в upd_ru поле account описано как к/с банка продавца
        "items": rows,
        "vat": _money(sum(r["vat_amount"] for r in rows)) if with_vat else None,
        "total": _money(sum(r["amount_with_vat"] for r in rows)),
    }


def _act(c: Company, number: str, issued: date, items: list[tuple[str, float]]) -> dict:
    rows = _rows(items)
    return {
        "number": number,
        "issued": issued.isoformat(),
        "contractor": c.name,
        "contractor_inn": c.inn,
        "customer": BUYER.name,
        "items": rows,
        "total": _money(sum(r["amount"] for r in rows)),
    }


@dataclass
class _When:
    days_ago: int
    hour: int
    issued: date = field(default=date.min)


def _workday_back(d: date) -> date:
    """Суббота и воскресенье → предыдущая пятница."""
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def _workday_forward(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def _workdays_before(d: date, n: int) -> date:
    while n:
        d -= timedelta(days=1)
        n -= d.weekday() < 5
    return d


def _when(rng: random.Random, today: date, *, days: tuple[int, int] = (1, 20), gap: tuple[int, int] = (0, 3)) -> _When:
    """Будний день в окне days (дней назад), час 9–17 МСК; дата документа — за gap рабочих дней до создания.

    Сегодняшний день не берётся (days ≥ 1): иначе момент мог бы оказаться позже «сейчас».
    """
    created = _workday_back(today - timedelta(days=rng.randint(*days)))
    if (today - created).days > days[1]:  # у края окна выходные — вперёд, на понедельник
        created = _workday_forward(created + timedelta(days=1))
    w = _When((today - created).days, rng.randint(9, 17))
    w.issued = _workdays_before(created, rng.randint(*gap))
    return w


def scenarios(today: date) -> list[Scenario]:
    """24 сценария: 17 ok, 6 needs_review, 1 failed (фото не читается).

    Три needs_review проверяет оператор: два одобряет, один (дубль) отклоняет — после проверок
    19 ok, 3 needs_review, 2 failed. Создание — по будним дням в 9–17 ч, без сегодняшнего дня.
    """
    rng = random.Random(SEED)
    out: list[Scenario] = []

    def add(key: str, schema: str, answer: dict | None, w: _When, expect: Expect = "ok", **kw) -> None:
        out.append(Scenario(key, schema, answer, w.days_ago, w.hour, expect, **kw))

    # --- счета ---
    w = _when(rng, today)
    add("inv_cement", "invoice_ru",
        _invoice(STROYKOMPLEKT, "СК-1043", w.issued, [("cement", 40), ("sand", 12), ("delivery", 2)]), w)
    w = _when(rng, today)
    add("inv_roof", "invoice_ru",
        _invoice(KROVLYA, "418", w.issued, [("profsheet", 180), ("screws", 6), ("membrane", 4)]), w)
    w = _when(rng, today)
    add("inv_timber", "invoice_ru", _invoice(LES, "ЛО-00297", w.issued, [("beam", 4.5), ("board", 3)]), w)
    w = _when(rng, today)
    add("inv_cable", "invoice_ru", _invoice(ELEKTRO, "ЭП-2210", w.issued, [("cable", 500), ("breaker", 12)]), w)
    w = _when(rng, today)
    add("inv_gravel", "invoice_ru",
        _invoice(STROYKOMPLEKT, "СК-1077", w.issued, [("gravel", 30), ("sand", 20), ("delivery", 3)]), w)
    w = _when(rng, today)
    add("inv_blocks", "invoice_ru",
        _invoice(STROYKOMPLEKT, "СК-1102", w.issued, [("gasblock", 24), ("brick", 2000)]), w)

    # крупный счёт (681 900 ₽) — согласование; на следующий рабочий день поставщик прислал его повторно.
    # Проверенные — не ближе 4 дней: проверка, перенесённая на следующий рабочий день, всё равно в прошлом
    w = _when(rng, today, days=(7, 20), gap=(0, 1))
    big = _invoice(METALL, "МП-2187", w.issued, [("rebar", 6.5), ("pipe", 300)])
    add("inv_metal_big", "invoice_ru", big, w, "needs_review", review="approve",
        review_note="Согласовано: арматура и труба под каркас склада")
    dup_day = _workday_forward(today - timedelta(days=w.days_ago - 1))
    dup = _When((today - dup_day).days, rng.randint(9, 17), w.issued)
    add("inv_metal_big_dup", "invoice_ru", dict(big), dup, "needs_review", review="reject",
        review_note=f"Дубль счёта № {big['number']}")

    # модель прочла 58 000 как 85 000 в первой строке: строка и «Итого» не сходятся, оператор правит
    w = _when(rng, today, days=(4, 20))
    wool = _invoice(KROVLYA, "431", w.issued, [("wool", 40), ("membrane", 6), ("delivery", 1)])
    misread = [dict(r) for r in wool["items"]]
    misread[0]["amount"] = 85000
    add("inv_wool_misread", "invoice_ru", {**wool, "items": misread}, w, "needs_review", review="approve_edit",
        review_note="Сумма первой строки исправлена по фото: 58 000", fix={"items": wool["items"]})

    w = _when(rng, today)
    add("blurry_photo", "invoice_ru", None, w, "failed")

    # --- УПД ---
    w = _when(rng, today)
    add("upd_cement", "upd_ru", _upd(STROYKOMPLEKT, "1121", w.issued, [("cement", 60), ("delivery", 1)]), w)
    w = _when(rng, today)
    add("upd_roof", "upd_ru", _upd(KROVLYA, "УПД-0388", w.issued, [("profsheet", 220), ("screws", 8)]), w)
    w = _when(rng, today)
    add("upd_concrete", "upd_ru", _upd(BETON, "БУ-745", w.issued, [("concrete", 18), ("pump", 4)]), w)
    w = _when(rng, today)
    add("upd_boards", "upd_ru", _upd(LES, "ЛО-00305", w.issued, [("board", 5)]), w)
    w = _when(rng, today)
    add("upd_cable", "upd_ru", _upd(ELEKTRO, "ЭП-2236", w.issued, [("cable", 300), ("breaker", 8)]), w)
    w = _when(rng, today)
    add("upd_excavator", "upd_ru", _upd(KARPENKO, "57", w.issued, [("excavator", 16)], with_vat=False), w)

    # «Всего к оплате» прочитано с перестановкой цифр: 276 552 вместо 267 552
    w = _when(rng, today)
    steel = _upd(METALL, "МП-2203", w.issued, [("steel", 2.4), ("pipe", 120)])
    add("upd_total_mismatch", "upd_ru", {**steel, "total": 276552}, w, "needs_review")
    # 595 920 ₽ — выше порога согласования
    w = _when(rng, today)
    add("upd_concrete_big", "upd_ru", _upd(BETON, "БУ-781", w.issued, [("concrete", 64), ("pump", 10)]), w,
        "needs_review")

    # --- акты ---
    w = _when(rng, today)
    add("act_roof_work", "act_works", _act(KARPENKO.company, "14", w.issued, [("roof_work", 180)]), w)
    w = _when(rng, today)
    add("act_steel_work", "act_works", _act(KARPENKO.company, "15", w.issued, [("steel_work", 6.5)]), w)
    w = _when(rng, today)
    add("act_cleanup", "act_works", _act(SAVELIEVA.company, "А-092", w.issued, [("cleanup", 40), ("waste", 3)]), w)
    w = _when(rng, today)
    add("act_electro", "act_works", _act(ELEKTRO.company, "ЭП-А-118", w.issued, [("electro_work", 64)]), w)
    w = _when(rng, today)
    add("act_unloading", "act_works", _act(SAVELIEVA.company, "А-097", w.issued, [("unloading", 12)]), w)
    # подрядчика нет в справочнике поставщиков
    w = _when(rng, today)
    add("act_unknown_contractor", "act_works", _act(_OUTSIDER, "37", w.issued, [("base_work", 85)]), w,
        "needs_review")
    return out


class DemoExtractor:
    """Отдаёт заготовленный ответ вместо модели; None — фото не читается."""

    model = "demo"

    def __init__(self, answer: dict | None):
        self.answer = answer

    def extract(self, schema: DocSchema, images: list[bytes]) -> Extraction:
        if self.answer is None:
            raise ExtractionError("фото слишком размыто, текст не читается")
        return Extraction(raw=dict(self.answer), model=self.model, input_tokens=0, output_tokens=0)


@dataclass(frozen=True)
class DemoDestinations:
    mail_accounting: uuid.UUID
    mail_operator: uuid.UUID
    sheets: uuid.UUID | None = None
    bitrix: uuid.UUID | None = None
    telegram: uuid.UUID | None = None


def _ids(*ids: uuid.UUID | None) -> list[str]:
    return [str(i) for i in ids if i is not None]


def RULES_TEMPLATE(dest: DemoDestinations) -> list[dict]:  # noqa: N802 — имя из плана
    """Правила маршрутизации в формате RulesIn; schema одна на правило, поэтому счета и УПД — парами."""
    to_accounting = {"export_to": _ids(dest.sheets, dest.bitrix), "notify": _ids(dest.mail_accounting)}
    big = {"conditions": [{"field": "total", "op": "gt", "value": APPROVAL_LIMIT}], "require_approval": True}
    return [
        {"name": "Счета и УПД → учёт", "schema": "invoice_ru", **to_accounting},
        {"name": "Счета и УПД → учёт", "schema": "upd_ru", **to_accounting},
        {"name": "Крупная сумма — согласование", "schema": "invoice_ru", **big},
        {"name": "Крупная сумма — согласование", "schema": "upd_ru", **big},
        {
            "name": "Акты ключевых подрядчиков",
            "schema": "act_works",
            "conditions": [{"field": "contractor", "op": "in_dictionary", "value": "suppliers"}],
            "export_to": _ids(dest.sheets),
            # бухгалтерия узнаёт и об актах, как о счетах и УПД
            "notify": _ids(dest.mail_accounting),
        },
        {"name": "На проверку — оператору", "notify": _ids(dest.mail_operator, dest.telegram)},
    ]
