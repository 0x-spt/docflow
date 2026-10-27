"""Страницы → один PDF; затем OCR добавляет невидимый текстовый слой (PDF/A)."""

import io
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

import img2pdf
import numpy as np
from PIL import Image

from docflow.imaging.prepare import Page

SCAN_DPI = 200  # без метаданных PDF получит размер «1 пиксель = 1 pt», а не реальный лист
SCAN_JPEG_QUALITY = 85


class OcrUnavailable(RuntimeError):
    """В окружении нет tesseract или ghostscript."""


class OcrEngine(Protocol):
    def add_text_layer(self, pdf: bytes) -> bytes: ...


class NoOcr:
    def add_text_layer(self, pdf: bytes) -> bytes:
        return pdf


class OcrMyPdf:
    def __init__(self, languages: str = "rus+eng"):
        self.languages = languages

    def add_text_layer(self, pdf: bytes) -> bytes:
        has_gs = shutil.which("gs") or shutil.which("gswin64c")
        if shutil.which("tesseract") is None or not has_gs:
            raise OcrUnavailable("Нужны tesseract и ghostscript")
        import ocrmypdf  # тяжёлый импорт — только когда OCR действительно нужен
        from ocrmypdf.exceptions import MissingDependencyError

        # ocrmypdf пишет в корневой логгер при каждом запуске; пусть шумит только на WARNING и выше
        logging.getLogger("ocrmypdf").setLevel(logging.WARNING)
        with tempfile.TemporaryDirectory() as tmp:
            src, dst = Path(tmp, "in.pdf"), Path(tmp, "out.pdf")
            src.write_bytes(pdf)
            try:
                ocrmypdf.ocr(
                    src,
                    dst,
                    language=self.languages,
                    output_type="pdfa",
                    optimize=1,
                    skip_text=True,
                    progress_bar=False,
                )
            except MissingDependencyError as exc:
                raise OcrUnavailable(str(exc)) from exc
            return dst.read_bytes()


def _is_bw(img: Image.Image) -> bool:
    return img.mode == "L" and bool(np.isin(np.asarray(img), (0, 255)).all())


def _encode(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    if _is_bw(img):
        # Почему PNG: на двухцветном тексте JPEG и больше по размеру, и мутнее (ореолы у штрихов),
        # а 1-битный PNG сжимается Flate в разы лучше и не портит края для OCR.
        img.convert("1").save(buf, format="PNG")
    else:
        img.convert("RGB" if img.mode == "RGB" else "L").save(buf, format="JPEG", quality=SCAN_JPEG_QUALITY)
    return buf.getvalue()


def build_pdf(pages: list[Page], ocr: OcrEngine) -> bytes:
    if not pages:
        raise ValueError("Нет страниц для PDF")
    layout = img2pdf.get_fixed_dpi_layout_fun((SCAN_DPI, SCAN_DPI))
    pdf = img2pdf.convert([_encode(p.scan) for p in pages], layout_fun=layout)
    return ocr.add_text_layer(pdf)
