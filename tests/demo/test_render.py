from datetime import date
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image

from docflow.demo import render
from docflow.demo.data import scenarios
from docflow.imaging import find_sheet


@pytest.fixture
def font():
    try:
        return render.find_font()
    except render.FontMissing:
        pytest.skip("не найден шрифт DejaVu")


def _scenario(key: str):
    return next(s for s in scenarios(date(2026, 10, 29)) if s.key == key)


def test_render_is_jpeg_and_deterministic(font):
    sc = _scenario("inv_cement")
    a = render.render_photo(sc, "Счёт на оплату", seed=1)
    b = render.render_photo(sc, "Счёт на оплату", seed=1)
    assert a == b
    assert a[:2] == b"\xff\xd8"


@pytest.mark.parametrize("key", ["inv_cement", "upd_cement", "act_roof_work", "blurry_photo"])
def test_sheet_found(font, key):
    jpeg = render.render_photo(_scenario(key), "Документ", seed=2)
    img = Image.open(BytesIO(jpeg)).convert("RGB")
    corners = find_sheet(img)
    assert corners is not None
    assert len(corners) == 4
    xs, ys = corners[:, 0], corners[:, 1]
    # площадь по формуле Гаусса
    area = abs(sum(xs[i] * ys[(i + 1) % 4] - xs[(i + 1) % 4] * ys[i] for i in range(4))) / 2
    assert area > 0.4 * img.width * img.height


def test_render_without_font_message(monkeypatch, tmp_path: Path):
    from docflow.config import get_settings

    monkeypatch.setenv("DOCFLOW_PDF_FONT_PATH", str(tmp_path / "нет.ttf"))
    get_settings.cache_clear()
    monkeypatch.setattr(render, "_SYSTEM_FONTS", (str(tmp_path / "нет2.ttf"),))
    with pytest.raises(render.FontMissing, match="fonts-dejavu-core"):
        render.find_font()
    get_settings.cache_clear()
