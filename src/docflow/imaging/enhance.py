"""Режимы скана для PDF: цветной, серый и чёрно-белый."""

from typing import Literal

import cv2
import numpy as np
from PIL import Image

Mode = Literal["color", "gray", "bw"]

# Адаптивный порог: окно 35 px (нечётное) больше толщины штриха, но меньше бланка, чтобы
# подстраиваться под локальный фон; C=15 — запас, чтобы остаточный шум бумаги не стал точками.
BW_BLOCK = 35
BW_C = 15
FLATTEN_SIDE = 200  # освещение оцениваем на миниатюре: размытие большого кадра дорого и не нужно


def _flatten(gray: np.ndarray) -> np.ndarray:
    """Выравнивает освещение: делит на сильно размытую копию, то есть на «карту» теней и бликов.

    Бумага становится ровно белой, а тёмный текст остаётся тёмным.
    """
    scale = FLATTEN_SIDE / max(gray.shape)
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    background = cv2.GaussianBlur(small, (0, 0), sigmaX=FLATTEN_SIDE / 8)
    background = cv2.resize(background, (gray.shape[1], gray.shape[0]), interpolation=cv2.INTER_LINEAR)
    return cv2.divide(gray, np.maximum(background, 1), scale=255)


def enhance(img: Image.Image, mode: Mode) -> Image.Image:
    if mode == "color":
        return img.convert("RGB")
    flat = _flatten(cv2.cvtColor(np.asarray(img.convert("RGB")), cv2.COLOR_RGB2GRAY))
    if mode == "gray":
        return Image.fromarray(flat)
    if mode == "bw":
        bw = cv2.adaptiveThreshold(
            flat, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, BW_BLOCK, BW_C
        )
        return Image.fromarray(bw)
    raise ValueError(f"Неизвестный режим скана: {mode}")
