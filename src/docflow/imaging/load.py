"""Чтение загруженных файлов в картинки.

Тип определяется по первым байтам, а не по расширению: клиенты присылают HEIC с именем .jpg,
а мусор с именем .pdf. Расширению верить нельзя.
"""

import io

import numpy as np
import pillow_heif
import pypdfium2 as pdfium
from PIL import Image, ImageOps, ImageSequence

pillow_heif.register_heif_opener()  # после этого Image.open понимает HEIC/HEIF с iPhone

# Потолок длинной стороны любой декодированной страницы (и фото, и PDF): 3000 px — около 250 dpi
# для A4, с запасом хватает OCR, а 30 страниц в памяти не раздуваются до гигабайт
MAX_DECODED_SIDE = 3000
PDF_DPI = 200  # хватает и для OCR, и для модели; выше — раздувает память без выигрыша в тексте
HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"heif", b"mif1", b"msf1"}


class UnsupportedFile(ValueError):
    """Файл нельзя принять: неизвестный формат, битый, слишком большой или страниц больше лимита."""


def detect_kind(data: bytes) -> str | None:
    """'pdf', 'image' или None, если сигнатура неизвестна."""
    if data.startswith(b"%PDF-"):
        return "pdf"
    is_image = (
        data.startswith((b"\xff\xd8\xff", b"\x89PNG", b"II*\x00", b"MM\x00*"))
        or (data[:4] == b"RIFF" and data[8:12] == b"WEBP")
        or (data[4:8] == b"ftyp" and data[8:12] in HEIF_BRANDS)
    )
    return "image" if is_image else None


def _reject(filename: str, why: str) -> UnsupportedFile:
    return UnsupportedFile(f"Файл «{filename}» не удалось принять: {why}")


def count_pages(data: bytes, filename: str) -> int:
    """Число страниц без декодирования: нужно, чтобы отсечь лишнее до тяжёлой обработки."""
    kind = detect_kind(data)
    if kind is None:
        raise _reject(filename, "неподдерживаемый формат (нужны JPEG, PNG, WEBP, TIFF, HEIC или PDF)")
    if kind == "image":
        try:
            with Image.open(io.BytesIO(data)) as img:
                return getattr(img, "n_frames", 1)  # многостраничный TIFF — по странице на кадр
        except Image.DecompressionBombError as exc:
            raise _reject(filename, "слишком большое изображение") from exc
        except (OSError, ValueError, SyntaxError) as exc:
            raise _reject(filename, "изображение повреждено") from exc
    pdf = None
    try:
        pdf = pdfium.PdfDocument(data)
        return len(pdf)
    except pdfium.PdfiumError as exc:
        raise _reject(filename, "PDF повреждён или защищён паролем") from exc
    finally:
        if pdf is not None:
            pdf.close()


def _to_rgb(img: Image.Image) -> Image.Image:
    """В RGB без потери контраста.

    Pillow при convert("RGB") обрезает 16-битные и float-кадры (сканерные TIFF) по 255, и всё
    становится белым. Поэтому для них сами растягиваем реальный диапазон значений на 0..255.
    """
    if img.mode.startswith("I") or img.mode == "F":
        arr = np.asarray(img).astype(np.float64)
        lo, hi = float(arr.min()), float(arr.max())
        arr = (arr - lo) * (255.0 / (hi - lo)) if hi > lo else np.full_like(arr, 255.0)
        img = Image.fromarray(arr.round().astype(np.uint8), "L")
    return img.convert("RGB")


def _cap(img: Image.Image) -> Image.Image:
    if max(img.size) > MAX_DECODED_SIDE:
        img.thumbnail((MAX_DECODED_SIDE, MAX_DECODED_SIDE), Image.Resampling.LANCZOS)
    return img


def _load_raster(data: bytes, filename: str) -> list[Image.Image]:
    try:
        with Image.open(io.BytesIO(data)) as img:
            # Телефон хранит поворот в EXIF, а не в пикселях; кадры — страницы многостраничного TIFF
            return [
                _cap(_to_rgb(ImageOps.exif_transpose(frame)))
                for frame in ImageSequence.Iterator(img)
            ]
    except Image.DecompressionBombError as exc:
        raise _reject(filename, "слишком большое изображение") from exc
    except (OSError, ValueError, SyntaxError) as exc:  # Pillow бросает разное на битых файлах
        raise _reject(filename, "изображение повреждено") from exc


def _load_pdf(data: bytes, filename: str) -> list[Image.Image]:
    pages = []
    pdf = None
    try:
        pdf = pdfium.PdfDocument(data)
        for page in pdf:
            # Масштаб считаем по странице: плакат 3000x3000 pt при 200 dpi дал бы ~200 МБ на страницу
            width, height = page.get_size()
            scale = min(PDF_DPI / 72, MAX_DECODED_SIDE / max(width, height))
            pages.append(page.render(scale=scale).to_pil().convert("RGB"))
    except pdfium.PdfiumError as exc:
        raise _reject(filename, "PDF повреждён или защищён паролем") from exc
    finally:
        if pdf is not None:
            pdf.close()
    return pages


def load_images(data: bytes, filename: str) -> list[Image.Image]:
    """Одна картинка для фото, по картинке на страницу для PDF."""
    kind = detect_kind(data)
    if kind is None:
        raise _reject(filename, "неподдерживаемый формат (нужны JPEG, PNG, WEBP, TIFF, HEIC или PDF)")
    if kind == "pdf":
        return _load_pdf(data, filename)
    return _load_raster(data, filename)
