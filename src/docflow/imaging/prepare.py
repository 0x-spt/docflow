"""Файлы клиента → страницы: чистый скан для PDF и цветной JPEG для модели."""

import io
from dataclasses import dataclass

from PIL import Image

from docflow.imaging.enhance import Mode, enhance
from docflow.imaging.load import UnsupportedFile, count_pages, load_images
from docflow.imaging.sheet import JPEG_QUALITY, MAX_SIDE, find_sheet, warp

# Меняй при любой правке, влияющей на итоговую картинку: версия входит в ключ кэша ответов
PREPROCESS_VERSION = "1"


@dataclass(frozen=True)
class Page:
    scan: Image.Image  # идёт в выходной PDF
    model_jpeg: bytes  # цветной, длинная сторона <= MAX_SIDE; идёт в модель
    cropped: bool  # нашёлся ли лист; если нет — берём кадр целиком


def _to_jpeg(img: Image.Image) -> bytes:
    img = img.copy()
    img.thumbnail((MAX_SIDE, MAX_SIDE))  # сохраняет пропорции, только уменьшает
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=JPEG_QUALITY)
    return buf.getvalue()


def prepare_pages(files: list[tuple[bytes, str]], mode: Mode = "gray", max_pages: int = 30) -> list[Page]:
    # Число страниц считаем до декодирования: 31-я страница не должна стоить минуты CPU
    total = sum(count_pages(data, name) for data, name in files)
    if total > max_pages:
        raise UnsupportedFile(f"Слишком много страниц: {total}, допустимо не больше {max_pages}")

    pages = []
    for data, name in files:
        for img in load_images(data, name):
            corners = find_sheet(img)
            if corners is not None:
                img = warp(img, corners)
            pages.append(Page(scan=enhance(img, mode), model_jpeg=_to_jpeg(img), cropped=corners is not None))
    return pages
