import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.db.models import Base
from docflow.db.session import make_engine, make_sessionmaker


@pytest.fixture
async def session():
    engine = make_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        # SQLite по умолчанию не проверяет внешние ключи — включаем, чтобы поведение совпало с Postgres
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with make_sessionmaker(engine)() as s:
        yield s
    await engine.dispose()


__all__ = ["AsyncSession"]


_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "C:/Windows/Fonts/DejaVuSans.ttf",
    "C:/Windows/Fonts/arial.ttf",
]


@pytest.fixture
def digital_font(monkeypatch):
    """Шрифт с кириллицей для цифрового PDF: по настройке, затем в системе; иначе тест пропускается.
    Ставить раньше `api` в аргументах: шрифт читается из окружения при создании настроек."""
    from pathlib import Path

    from docflow.config import Settings, get_settings

    for cand in [Settings().pdf_font_path, *_FONT_CANDIDATES]:
        if Path(cand).exists():
            monkeypatch.setenv("DOCFLOW_PDF_FONT_PATH", cand)
            get_settings.cache_clear()
            yield cand
            get_settings.cache_clear()
            return
    pytest.skip("не найден шрифт с кириллицей для цифрового PDF")
