import uuid

from docflow.db.models import Lead
from docflow.notify import TG_TEXT_MAX, lead_text


def _lead(**kw) -> Lead:
    base = {"name": "Иван", "contact": "@ivan", "volume": "lt100"}
    return Lead(id=uuid.uuid4(), **{**base, **kw})


def test_short_lead_untouched():
    text = lead_text(_lead(company="ООО Ромашка", message="Привет"))
    assert "Компания: ООО Ромашка" in text
    assert "Сообщение: Привет" in text
    assert "…" not in text


def test_max_lengths_fit_telegram():
    lead = _lead(
        name="я" * 100,
        company="к" * 200,
        contact="c" * 200,
        niche="н" * 100,
        documents="д" * 2000,
        message="м" * 2000,
    )
    text = lead_text(lead)
    assert len(text) <= TG_TEXT_MAX <= 4000
    # короткие поля целиком, длинные обрезаны с многоточием
    assert "я" * 100 in text and "c" * 200 in text
    assert "Документы: " in text and "Сообщение: " in text
    assert text.count("…") >= 1


def test_lead_text_targets_in_russian():
    text = lead_text(_lead(targets=["1c", "google_sheets", "unknown"]))
    assert "Куда выгружать: 1С, Google Таблицы, Пока не знаю" in text


def test_lead_text_without_targets():
    assert "Куда выгружать" not in lead_text(_lead(targets=[]))
    assert "Куда выгружать" not in lead_text(_lead())
