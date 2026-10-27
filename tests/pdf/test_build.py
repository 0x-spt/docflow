import shutil

import pypdfium2 as pdfium
import pytest
from PIL import Image, ImageDraw, ImageFont

from docflow.imaging.prepare import Page
from docflow.pdf.build import NoOcr, OcrMyPdf, OcrUnavailable, build_pdf

FONT_CANDIDATES = [
    "DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


def _page(img: Image.Image) -> Page:
    return Page(scan=img, model_jpeg=b"", cropped=True)


def _cyrillic_font(size: int) -> ImageFont.FreeTypeFont:
    for name in FONT_CANDIDATES:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    pytest.skip("не найден TrueType-шрифт с кириллицей")


def test_page_count():
    pages = [_page(Image.new("RGB", (400, 600), "white")) for _ in range(3)]
    pdf = build_pdf(pages, NoOcr())
    assert len(pdfium.PdfDocument(pdf)) == 3


def test_empty_pages_rejected():
    with pytest.raises(ValueError):
        build_pdf([], NoOcr())


def test_real_paper_size_at_200_dpi():
    pdf = build_pdf([_page(Image.new("L", (1654, 2339), 255))], NoOcr())  # A4 при 200 dpi
    width, height = pdfium.PdfDocument(pdf)[0].get_size()
    assert width == pytest.approx(595, abs=2)
    assert height == pytest.approx(842, abs=2)


def test_all_modes_and_bw_is_png():
    bw = Image.new("L", (400, 600), 255)
    bw.paste(0, (10, 10, 100, 50))
    pages = [_page(Image.new("RGB", (400, 600), "white")), _page(Image.new("L", (400, 600), 128)), _page(bw)]
    pdf = build_pdf(pages, NoOcr())
    assert len(pdfium.PdfDocument(pdf)) == 3
    assert b"/DCTDecode" in pdf and b"/FlateDecode" in pdf  # цвет/серый — JPEG, ч/б — PNG (Flate)


def test_ocr_unavailable_without_tesseract(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(OcrUnavailable):
        OcrMyPdf().add_text_layer(b"%PDF-1.4")


@pytest.mark.integration
def test_text_layer_searchable():
    if shutil.which("tesseract") is None:
        pytest.skip("tesseract не установлен")
    img = Image.new("L", (1654, 2339), 255)
    ImageDraw.Draw(img).text((200, 300), "СЧЁТ НА ОПЛАТУ", font=_cyrillic_font(120), fill=0)
    pdf = build_pdf([_page(img)], OcrMyPdf())
    text = pdfium.PdfDocument(pdf)[0].get_textpage().get_text_range().upper()
    assert "СЧЕТ" in text or "СЧЁТ" in text
