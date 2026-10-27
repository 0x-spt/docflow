import io

import img2pdf
import numpy as np
import pytest
from conftest import make_sheet_photo, to_bytes
from PIL import Image

from docflow.imaging import PREPROCESS_VERSION, UnsupportedFile, enhance, prepare_pages


def test_version():
    assert PREPROCESS_VERSION == "1"


def test_sheet_page_is_cropped(sheet_jpeg):
    [page] = prepare_pages([(sheet_jpeg, "a.jpg")])
    assert page.cropped is True
    assert abs(page.scan.width / page.scan.height - 0.75) < 0.05


def test_no_sheet_keeps_frame(noise_image):
    [page] = prepare_pages([(to_bytes(noise_image, "PNG"), "n.png")])
    assert page.cropped is False
    assert page.scan.size == noise_image.size


def test_model_jpeg_bounded():
    big = make_sheet_photo(3000, 2500).resize((3000, 2500))
    [page] = prepare_pages([(to_bytes(big), "big.jpg")])
    jpeg = Image.open(io.BytesIO(page.model_jpeg))
    assert jpeg.format == "JPEG"
    assert max(jpeg.size) <= 1568
    assert jpeg.mode == "RGB"  # модель получает цветную картинку при любом режиме скана


def test_too_many_pages(noise_image):
    data = to_bytes(noise_image, "PNG")
    with pytest.raises(UnsupportedFile):
        prepare_pages([(data, "1.png"), (data, "2.png"), (data, "3.png")], max_pages=2)


def test_too_many_pages_counts_pdf_pages():
    page = to_bytes(Image.new("RGB", (100, 100), "white"), "PNG")
    pdf = img2pdf.convert([page, page, page])
    with pytest.raises(UnsupportedFile):
        prepare_pages([(pdf, "a.pdf")], max_pages=2)


def test_order_kept_across_files_and_pdf_pages():
    def png(color):
        return to_bytes(Image.new("RGB", (100, 100), color), "PNG")

    pdf = img2pdf.convert([png((0, 0, 0)), png((255, 255, 255))])
    pages = prepare_pages([(png((128, 128, 128)), "a.png"), (pdf, "b.pdf")], mode="color")
    means = [float(np.asarray(p.scan).mean()) for p in pages]
    assert [round(m / 128) for m in means] == [1, 0, 2]


def test_modes_shapes(sheet_photo):
    assert enhance(sheet_photo, "color").mode == "RGB"
    assert enhance(sheet_photo, "gray").mode == "L"
    bw = enhance(sheet_photo, "bw")
    assert set(np.unique(np.asarray(bw))) <= {0, 255}
