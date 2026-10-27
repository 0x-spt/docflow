import img2pdf
import pytest
from conftest import to_bytes
from PIL import Image

from docflow.imaging import UnsupportedFile, load_images


def test_exif_rotation_applied():
    img = Image.new("RGB", (100, 50), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # Orientation: повернуть на 90° по часовой
    data = to_bytes(img, exif=exif)
    [loaded] = load_images(data, "photo.jpg")
    assert loaded.size == (50, 100)


def test_heic_loads():
    pillow_heif = pytest.importorskip("pillow_heif")
    img = Image.new("RGB", (64, 64), "red")
    try:
        data = to_bytes(img, "HEIF")
    except Exception as exc:  # в колесе нет кодировщика HEIF
        pytest.skip(f"HEIF-кодирование недоступно в установленном pillow-heif: {exc!r}")
    assert pillow_heif is not None
    assert len(load_images(data, "IMG_0001.heic")) == 1


def test_pdf_pages(tmp_path):
    pages = [to_bytes(Image.new("RGB", (200, 300), c), "PNG") for c in ("white", "gray")]
    pdf = img2pdf.convert(pages)
    assert len(load_images(pdf, "scan.pdf")) == 2


def test_type_detected_by_bytes_not_extension():
    data = to_bytes(Image.new("RGB", (20, 20), "white"), "PNG")
    assert len(load_images(data, "photo.jpg")) == 1


def test_garbage_rejected():
    with pytest.raises(UnsupportedFile, match="x.jpg"):
        load_images(b"hello", "x.jpg")


def test_empty_rejected():
    with pytest.raises(UnsupportedFile, match="empty.png"):
        load_images(b"", "empty.png")


def test_truncated_jpeg_rejected():
    with pytest.raises(UnsupportedFile, match="bad.jpg"):
        load_images(b"\xff\xd8\xff\xe0" + b"\x00" * 10, "bad.jpg")


def test_decompression_bomb_rejected(monkeypatch):
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 100)
    data = to_bytes(Image.new("RGB", (100, 100), "white"), "PNG")  # 10 000 px > 2*100
    with pytest.raises(UnsupportedFile, match="bomb.png"):
        load_images(data, "bomb.png")
