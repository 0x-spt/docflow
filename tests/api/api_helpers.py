"""Синтетические файлы для тестов API."""

import io

import img2pdf
from PIL import Image

BASE_URL = "https://api.example.test"


def png_bytes(size=(40, 60)) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buf, format="PNG")
    return buf.getvalue()


def pdf_bytes(pages: int = 2) -> bytes:
    return img2pdf.convert([png_bytes() for _ in range(pages)])
