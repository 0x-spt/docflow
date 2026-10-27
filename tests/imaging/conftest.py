"""Фикстуры-картинки генерируются в коде: бинарников в репозитории нет."""

import io

import cv2
import numpy as np
import pytest
from PIL import Image


def make_sheet_photo(width: int = 1200, height: int = 1000) -> Image.Image:
    """Белый лист 600x800 с «текстом» на тёмном фоне, снятый под перспективой."""
    sheet = np.full((800, 600, 3), 255, np.uint8)
    for y in range(60, 760, 50):
        cv2.putText(sheet, "TEXT 12345", (40, y), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
    frame = np.full((height, width, 3), 40, np.uint8)
    src = np.array([[0, 0], [599, 0], [599, 799], [0, 799]], np.float32)
    dst = np.array([[250, 100], [790, 130], [770, 890], [220, 850]], np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    cv2.warpPerspective(sheet, matrix, (width, height), dst=frame, borderMode=cv2.BORDER_TRANSPARENT)
    return Image.fromarray(frame)


def to_bytes(img: Image.Image, fmt: str = "JPEG", **kwargs) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format=fmt, **kwargs)
    return buf.getvalue()


@pytest.fixture
def sheet_photo() -> Image.Image:
    return make_sheet_photo()


@pytest.fixture
def sheet_jpeg(sheet_photo) -> bytes:
    return to_bytes(sheet_photo)


@pytest.fixture
def noise_image() -> Image.Image:
    rng = np.random.default_rng(0)
    return Image.fromarray(rng.integers(0, 256, (400, 300, 3), dtype=np.uint8))
