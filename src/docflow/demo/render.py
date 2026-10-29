"""Синтетическое фото документа: лист А4 с таблицей, печатью и подписью на столе (для демо-тенанта)."""
import io
import math
import random
from datetime import date
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from docflow.config import Settings
from docflow.demo.data import BUYER, STROYKOMPLEKT, Scenario, _invoice
from docflow.demo.requisites import DEMO_BANK

SHEET_W, SHEET_H = 1240, 1754
TABLE_W, TABLE_H = 1600, 2100
MARGIN = 80
PAPER = (252, 252, 250)
INK = (22, 22, 26)
BLUE = (28, 52, 150)

_SYSTEM_FONTS = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
)


class FontMissing(RuntimeError):
    pass


def find_font() -> Path:
    """Обычный DejaVu: из настроек, затем из системных каталогов."""
    for cand in (Settings().pdf_font_path, *_SYSTEM_FONTS):
        if Path(cand).exists():
            return Path(cand)
    raise FontMissing("нет шрифта DejaVu: установите fonts-dejavu-core или задайте DOCFLOW_PDF_FONT_PATH")


class _Fonts:
    def __init__(self) -> None:
        self.regular = find_font()
        bold = self.regular.with_name("DejaVuSans-Bold.ttf")
        self.bold = bold if bold.exists() else self.regular

    def get(self, size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
        return ImageFont.truetype(str(self.bold if bold else self.regular), size)


def _num(x: float | int | None) -> str:
    """12500 → «12 500,00»."""
    if x is None:
        return ""
    return f"{x:,.2f}".replace(",", " ").replace(".", ",")


def _qty(x: float | int) -> str:
    return str(int(x)) if float(x) == int(x) else str(x).replace(".", ",")


def _date(iso: str | None) -> str:
    return date.fromisoformat(iso).strftime("%d.%m.%Y") if iso else ""


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> list[str]:
    lines: list[str] = []
    for para in text.split("\n"):
        line = ""
        for word in para.split():
            trial = f"{line} {word}".strip()
            if line and draw.textlength(trial, font=font) > width:
                lines.append(line)
                line = word
            else:
                line = trial
        lines.append(line)
    return lines


class _Page:
    """Лист: курсор по вертикали и примитивы вёрстки."""

    def __init__(self, fonts: _Fonts) -> None:
        self.img = Image.new("RGB", (SHEET_W, SHEET_H), PAPER)
        self.d = ImageDraw.Draw(self.img)
        self.f = fonts
        self.y = 90

    def line(self, text: str, size: int = 22, bold: bool = False) -> None:
        font = self.f.get(size, bold)
        for part in _wrap(self.d, text, font, SHEET_W - 2 * MARGIN):
            self.d.text((MARGIN, self.y), part, font=font, fill=INK)
            self.y += size + 10

    def cell(self, box: tuple[int, int, int, int], text: str, size: int = 19, bold: bool = False,
             align: str = "left") -> None:
        x0, y0, x1, y1 = box
        self.d.rectangle(box, outline=INK, width=2)
        font = self.f.get(size, bold)
        lines = _wrap(self.d, text, font, x1 - x0 - 16)
        ty = y0 + 8
        if y1 - y0 < 60:  # низкая ячейка: текст по центру по вертикали
            ty = y0 + (y1 - y0 - len(lines) * (size + 6)) // 2 + 3
        for part in lines:
            w = self.d.textlength(part, font=font)
            tx = {"left": x0 + 8, "right": x1 - 8 - w, "center": (x0 + x1 - w) / 2}[align]
            self.d.text((tx, ty), part, font=font, fill=INK)
            ty += size + 6

    def table(self, widths: list[int], header: list[str], rows: list[list[str]], aligns: list[str],
              size: int = 19) -> None:
        font = self.f.get(size)
        hfont = self.f.get(size - 2, True)

        def height(cells: list[str], fnt) -> int:
            n = max(len(_wrap(self.d, c, fnt, w - 16)) for c, w in zip(cells, widths, strict=True))
            return n * (size + 6) + 30

        top = self.y
        h = height(header, hfont)
        x = MARGIN
        for w, c in zip(widths, header, strict=True):
            self.d.rectangle((x, top, x + w, top + h), fill=(236, 236, 232))
            self.cell((x, top, x + w, top + h), c, size - 2, True, "center")
            x += w
        top += h
        for row in rows:
            h = height(row, font)
            x = MARGIN
            for w, c, al in zip(widths, row, aligns, strict=True):
                self.cell((x, top, x + w, top + h), c, size, False, al)
                x += w
            top += h
        self.y = top + 24

    def totals(self, pairs: list[tuple[str, str]]) -> None:
        right = SHEET_W - MARGIN
        for label, value in pairs:
            font = self.f.get(22, label.startswith("Всего"))
            lw = self.d.textlength(label, font=font)
            vw = self.d.textlength(value, font=font)
            self.d.text((right - 190 - lw, self.y), label, font=font, fill=INK)
            self.d.text((right - vw, self.y), value, font=font, fill=INK)
            self.y += 36
        self.y += 10


def _stamp(name: str, inn: str, fonts: _Fonts, rng: random.Random, size: int = 250) -> Image.Image:
    """Круглая синяя печать: название по верхней дуге, ИНН в центре."""
    s = size * 2  # рисуем вдвое крупнее и уменьшаем: сглаживание
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    c = s / 2
    ink = (*BLUE, 215)
    for r, w in ((c - 6, 8), (c - 22, 4), (c * 0.55, 4)):
        d.ellipse((c - r, c - r, c + r, c + r), outline=ink, width=w)
    font = fonts.get(34, True)
    text = name.replace("«", "").replace("»", "").upper()
    step = min(math.radians(250) / max(len(text), 1), math.radians(13))
    start = -math.pi / 2 - step * (len(text) - 1) / 2
    rad = c - 66
    for i, ch in enumerate(text):
        a = start + i * step
        glyph = Image.new("RGBA", (60, 60), (0, 0, 0, 0))
        ImageDraw.Draw(glyph).text((30, 30), ch, font=font, fill=ink, anchor="mm")
        glyph = glyph.rotate(-math.degrees(a + math.pi / 2), resample=Image.BICUBIC)
        im.alpha_composite(glyph, (round(c + rad * math.cos(a) - 30), round(c + rad * math.sin(a) - 30)))
    small = fonts.get(30, True)
    for i, t in enumerate(("ИНН", inn)):
        d.text((c, c - 22 + i * 44), t, font=small, fill=ink, anchor="mm")
    # ровная печать выглядит фальшиво: чуть вращаем и «недожимаем» краску
    im = im.rotate(rng.uniform(-7, 7), resample=Image.BICUBIC).resize((size, size), Image.LANCZOS)
    alpha = np.asarray(im.getchannel("A"), dtype=np.float32)
    grain = np.random.default_rng(rng.randrange(1 << 30)).uniform(0.55, 1.0, alpha.shape)
    im.putalpha(Image.fromarray((alpha * grain).astype(np.uint8)))
    return im.filter(ImageFilter.GaussianBlur(0.6))


def _signature(rng: random.Random, w: int = 190, h: int = 70) -> Image.Image:
    """Подпись — сглаженная ломаная росчерка."""
    im = Image.new("RGBA", (w * 2, h * 2), (0, 0, 0, 0))
    pts = [(10.0, h * 1.6)]
    for i in range(1, 9):
        pts.append((10 + i * (w * 2 - 40) / 8, rng.uniform(0.15, 0.95) * h * 2))
    pts.append((w * 2 - 10.0, rng.uniform(0.9, 1.3) * h))
    for _ in range(3):  # сглаживание Чайкина
        nxt = [pts[0]]
        for a, b in zip(pts, pts[1:], strict=False):
            nxt += [(0.75 * a[0] + 0.25 * b[0], 0.75 * a[1] + 0.25 * b[1]),
                    (0.25 * a[0] + 0.75 * b[0], 0.25 * a[1] + 0.75 * b[1])]
        pts = [*nxt, pts[-1]]
    ImageDraw.Draw(im).line(pts, fill=(25, 40, 140, 235), width=4, joint="curve")
    return im.resize((w, h), Image.LANCZOS)


def _signature_block(page: _Page, rng: random.Random, labels: list[str], stamp_name: str, stamp_inn: str) -> None:
    y = page.y + 30
    font = page.f.get(21, True)
    for label, x in zip(labels, (MARGIN, SHEET_W // 2 + 20), strict=False):
        page.d.text((x, y + 30), label, font=font, fill=INK)
        lx = x + page.d.textlength(label, font=font) + 14
        page.d.line((lx, y + 58, lx + 230, y + 58), fill=INK, width=2)
        sig = _signature(rng)
        page.img.paste(sig, (round(lx + 20), y - 8), sig)
    st = _stamp(stamp_name, stamp_inn, page.f, rng)
    page.img.paste(st, (MARGIN + 200, y + 50), st)
    page.y = y + 260


def _items(a: dict) -> list[dict]:
    return a.get("items") or []


def _invoice_page(page: _Page, a: dict, title: str, rng: random.Random) -> None:
    d = page.d
    inner = SHEET_W - 2 * MARGIN
    x0, y0 = MARGIN, page.y
    left, lab = 640, 110
    kpp = a.get("supplier_kpp") or ""
    page.cell((x0, y0, x0 + left, y0 + 100), f"Банк получателя\n{DEMO_BANK.name}", 19)
    page.cell((x0 + left, y0, x0 + left + lab, y0 + 50), "БИК", 19)
    page.cell((x0 + left + lab, y0, x0 + inner, y0 + 50), a.get("bik") or "", 19)
    page.cell((x0 + left, y0 + 50, x0 + left + lab, y0 + 100), "Сч. №", 19)
    page.cell((x0 + left + lab, y0 + 50, x0 + inner, y0 + 100), a.get("corr_account") or "", 19)
    page.cell((x0, y0 + 100, x0 + left, y0 + 150), f"ИНН {a.get('supplier_inn') or ''}   КПП {kpp}", 19)
    page.cell((x0, y0 + 150, x0 + left, y0 + 250), f"Получатель\n{a.get('supplier') or ''}", 19)
    page.cell((x0 + left, y0 + 100, x0 + left + lab, y0 + 250), "Сч. №", 19)
    page.cell((x0 + left + lab, y0 + 100, x0 + inner, y0 + 250), a.get("account") or "", 19)
    page.y = y0 + 250 + 50
    page.line(f"{title} № {a.get('number') or ''} от {_date(a.get('issued'))}", 32, True)
    d.line((MARGIN, page.y, SHEET_W - MARGIN, page.y), fill=INK, width=3)
    page.y += 22
    page.line(f"Поставщик: {a.get('supplier') or ''}, ИНН {a.get('supplier_inn') or ''}"
              + (f", КПП {kpp}" if kpp else ""), 21)
    page.line(f"Покупатель: {a.get('buyer') or BUYER.name}, ИНН {a.get('buyer_inn') or BUYER.inn}"
              f", КПП {BUYER.kpp}, {BUYER.address}", 21)
    page.y += 20
    rows = [[str(i), r["name"], _qty(r["qty"]), r["unit"], _num(r["price"]), _num(r["amount"])]
            for i, r in enumerate(_items(a), 1)]
    page.table([50, 500, 100, 90, 140, 200], ["№", "Наименование", "Кол-во", "Ед.", "Цена", "Сумма"], rows,
               ["center", "left", "right", "center", "right", "right"], 20)
    page.totals([("Итого:", _num(a.get("subtotal"))), ("В том числе НДС (20%):", _num(a.get("vat"))),
                 ("Всего к оплате:", _num(a.get("total")))])
    page.line(f"Всего наименований {len(rows)}, на сумму {_num(a.get('total'))} руб.", 20)
    _signature_block(page, rng, ["Руководитель", "Бухгалтер"], a.get("supplier") or "", a.get("supplier_inn") or "")


def _upd_page(page: _Page, a: dict, title: str, rng: random.Random) -> None:
    d = page.d
    d.rectangle((MARGIN, page.y, MARGIN + 230, page.y + 80), outline=INK, width=2)
    d.text((MARGIN + 12, page.y + 8), f"Статус: {a.get('status') or ''}", font=page.f.get(24, True), fill=INK)
    d.text((MARGIN + 12, page.y + 46), "1 — СЧФ и ПД", font=page.f.get(16), fill=INK)
    d.text((MARGIN + 270, page.y + 22), title, font=page.f.get(25, True), fill=INK)
    page.y += 100
    page.line(f"Счёт-фактура № {a.get('number') or ''} от {_date(a.get('issued'))}", 28, True)
    page.line(f"Продавец: {a.get('supplier') or ''}", 20)
    page.line(f"ИНН/КПП продавца: {a.get('supplier_inn') or ''}/{a.get('supplier_kpp') or ''}".rstrip("/"), 20)
    page.line(f"Грузоотправитель и его адрес: {a.get('shipper') or ''}", 20)
    page.line(f"Грузополучатель и его адрес: {a.get('consignee') or ''}", 20)
    page.line(f"Покупатель: {a.get('buyer') or ''}", 20)
    page.line(f"ИНН/КПП покупателя: {a.get('buyer_inn') or ''}/{a.get('buyer_kpp') or ''}".rstrip("/"), 20)
    page.line(f"Банк: БИК {a.get('bik') or ''}, к/с {a.get('account') or ''}", 20)
    page.line("Валюта: наименование, код — российский рубль, 643", 20)
    page.y += 16
    rows = [[str(i), r["name"], r["unit"], _qty(r["qty"]), _num(r["price"]), _num(r["amount"]),
             _num(r["vat_amount"]) if r.get("vat_amount") is not None else "без НДС",
             _num(r.get("amount_with_vat"))] for i, r in enumerate(_items(a), 1)]
    page.table([40, 320, 60, 80, 120, 150, 130, 180],
               ["№", "Наименование товара (описание работ, услуг)", "Ед.", "Кол-во", "Цена (тариф)",
                "Стоимость без налога", "Сумма налога", "Стоимость с налогом"],
               rows, ["center", "left", "center", "right", "right", "right", "right", "right"], 18)
    page.totals([("Всего к оплате:", _num(a.get("total")))])
    _signature_block(page, rng, ["Руководитель", "Главный бухгалтер"], a.get("supplier") or "",
                     a.get("supplier_inn") or "")


def _act_page(page: _Page, a: dict, title: str, rng: random.Random) -> None:
    page.line(f"Акт № {a.get('number') or ''} от {_date(a.get('issued'))}", 32, True)
    page.line("об оказании услуг (выполнении работ)", 24)
    page.y += 20
    page.line(f"Исполнитель: {a.get('contractor') or ''}, ИНН {a.get('contractor_inn') or ''}", 21)
    page.line(f"Заказчик: {a.get('customer') or BUYER.name}", 21)
    page.y += 12
    page.line("Исполнитель выполнил, а Заказчик принял следующие работы (услуги):", 20)
    page.y += 8
    rows = [[str(i), r["name"], _qty(r["qty"]), r["unit"], _num(r["price"]), _num(r["amount"])]
            for i, r in enumerate(_items(a), 1)]
    page.table([50, 500, 100, 90, 140, 200], ["№", "Наименование работ", "Кол-во", "Ед.", "Цена", "Сумма"], rows,
               ["center", "left", "right", "center", "right", "right"], 20)
    page.totals([("Итого:", _num(a.get("total")))])
    page.line("Вышеперечисленные работы выполнены полностью и в срок. Заказчик претензий по объёму, качеству "
              "и срокам оказания услуг не имеет. НДС не облагается.", 20)
    _signature_block(page, rng, ["Исполнитель", "Заказчик"], a.get("contractor") or "",
                     a.get("contractor_inn") or "")


def _placeholder() -> dict:
    return _invoice(STROYKOMPLEKT, "СК-1000", date(2026, 1, 15), [("cement", 10), ("sand", 5)])


def _photograph(sheet: Image.Image, rng: random.Random, blur: bool) -> Image.Image:
    np_rng = np.random.default_rng(rng.randrange(1 << 30))
    # стол: тёмно-серый с пятнами света и шумом
    yy, xx = np.mgrid[0:TABLE_H, 0:TABLE_W].astype(np.float32)
    base = 62 + 14 * np.sin(xx / 170.0 + rng.random() * 6) * np.cos(yy / 230.0) + np_rng.normal(0, 7, xx.shape)
    table = Image.fromarray(np.clip(np.stack([base, base * 0.97, base * 0.93], -1), 0, 255).astype(np.uint8))

    # лист: поворот ±3°, лёгкая перспектива
    ang = math.radians(rng.uniform(-2, 2))
    cx, cy = TABLE_W / 2 + rng.uniform(-12, 12), TABLE_H / 2 + rng.uniform(-12, 12)
    quad = []
    for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1)):
        x, y = sx * SHEET_W / 2, sy * SHEET_H / 2
        quad.append((cx + x * math.cos(ang) - y * math.sin(ang) + rng.uniform(-7, 7),
                     cy + x * math.sin(ang) + y * math.cos(ang) + rng.uniform(-7, 7)))
    matrix = cv2.getPerspectiveTransform(
        np.float32([(0, 0), (SHEET_W, 0), (SHEET_W, SHEET_H), (0, SHEET_H)]), np.float32(quad))

    # неравномерное освещение листа
    gx = np.linspace(0, 1, SHEET_W, dtype=np.float32)[None, :]
    gy = np.linspace(0, 1, SHEET_H, dtype=np.float32)[:, None]
    light = 1.0 - 0.10 * (gx * rng.uniform(-1, 1) + gy * rng.uniform(0, 1))
    arr = np.asarray(sheet, dtype=np.float32) * light[..., None]
    sheet = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if blur:
        sheet = sheet.filter(ImageFilter.GaussianBlur(6))

    size = (TABLE_W, TABLE_H)
    warped = Image.fromarray(cv2.warpPerspective(np.asarray(sheet), matrix, size, flags=cv2.INTER_CUBIC))
    mask = Image.fromarray(cv2.warpPerspective(np.full((SHEET_H, SHEET_W), 255, np.uint8), matrix, size))
    shadow = mask.filter(ImageFilter.GaussianBlur(18)).point(lambda p: int(p * 0.55))
    table.paste(Image.new("RGB", table.size, (10, 10, 10)), (14, 20), shadow)
    table.paste(warped, (0, 0), mask)

    out = np.asarray(table, dtype=np.float32) + np_rng.normal(0, 4.5, (TABLE_H, TABLE_W, 1))
    photo = Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))
    return photo.filter(ImageFilter.GaussianBlur(0.7))


def render_photo(scenario: Scenario, schema_title: str, *, seed: int) -> bytes:
    """JPEG-«фото» документа сценария; для blurry_photo (ответа нет) — сильно размытый типовой счёт."""
    fonts = _Fonts()
    rng = random.Random(seed)
    page = _Page(fonts)
    draw = {"upd_ru": _upd_page, "act_works": _act_page}.get(scenario.schema, _invoice_page)
    title = "Счёт на оплату" if scenario.schema == "invoice_ru" else schema_title
    draw(page, scenario.answer or _placeholder(), title, rng)
    photo = _photograph(page.img, rng, blur=scenario.answer is None)
    buf = io.BytesIO()
    photo.save(buf, "JPEG", quality=82)
    return buf.getvalue()
