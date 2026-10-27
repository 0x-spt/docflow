"""Чтение загруженных файлов в картинки.

Тип определяется по первым байтам, а не по расширению: клиенты присылают HEIC с именем .jpg,
а мусор с именем .pdf. Расширению верить нельзя.
"""

import io

import pillow_heif
import pypdfium2 as pdfium
from PIL import Image, ImageOps

pillow_heif.register_heif_opener()  # после этого Image.open понимает HEIC/HEIF с iPhone

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
        return 1
    try:
        return len(pdfium.PdfDocument(data))
    except pdfium.PdfiumError as exc:
        raise _reject(filename, "PDF повреждён или защищён паролем") from exc


def _load_raster(data: bytes, filename: str) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as img:
            # Телефон хранит поворот в EXIF, а не в пикселях
            return ImageOps.exif_transpose(img).convert("RGB")
    except Image.DecompressionBombError as exc:
        raise _reject(filename, "слишком большое изображение") from exc
    except (OSError, ValueError, SyntaxError) as exc:  # Pillow бросает разное на битых файлах
        raise _reject(filename, "изображение повреждено") from exc


def _load_pdf(data: bytes, filename: str) -> list[Image.Image]:
    limit = Image.MAX_IMAGE_PIXELS
    pages = []
    try:
        pdf = pdfium.PdfDocument(data)
        for page in pdf:
            # Страница-«плакат» в PDF — та же бомба, только без декомпрессии: проверяем размер до рендера
            width, height = page.get_size()
            if limit and width * height * (PDF_DPI / 72) ** 2 > limit:
                raise _reject(filename, "слишком большая страница")
            pages.append(page.render(scale=PDF_DPI / 72).to_pil().convert("RGB"))
    except pdfium.PdfiumError as exc:
        raise _reject(filename, "PDF повреждён или защищён паролем") from exc
    return pages


def load_images(data: bytes, filename: str) -> list[Image.Image]:
    """Одна картинка для фото, по картинке на страницу для PDF."""
    kind = detect_kind(data)
    if kind is None:
        raise _reject(filename, "неподдерживаемый формат (нужны JPEG, PNG, WEBP, TIFF, HEIC или PDF)")
    if kind == "pdf":
        return _load_pdf(data, filename)
    return [_load_raster(data, filename)]
