import asyncio

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


@pytest.fixture
async def sm(tmp_path):
    """sessionmaker над файловой SQLite: для кода, который сам открывает сессии (конвейер, воркер)."""
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'db.sqlite').as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield make_sessionmaker(engine)
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


# --- фиктивные коннекторы -------------------------------------------------

from docflow.connectors import base as connectors_base  # noqa: E402


class FakeConnector(connectors_base.BaseConnector):
    """Фиктивный получатель для тестов: помнит вызовы, ошибки можно подложить.

    error — для test(); export_errors / notify_errors — очереди: каждый вызов забирает первую ошибку.
    """

    kind = "test_fake"
    label = "Тестовый"
    config_fields = [
        connectors_base.FieldDef("endpoint", "Адрес", required=True, type="url"),
        connectors_base.FieldDef("note", "Заметка"),
    ]
    secret_fields = [
        connectors_base.FieldDef("token", "Токен", required=True),
        connectors_base.FieldDef("hook", "Вебхук", type="url"),
    ]
    target_fields = [connectors_base.FieldDef("amount", "Сумма")]

    def __init__(self):
        self.calls = []
        self.error: Exception | None = None
        self.export_errors: list[Exception] = []
        self.notify_errors: list[Exception] = []
        self.external_id: str | None = "ext-1"
        self.export_delay: float = 0
        self.docs = []

    async def test(self, ctx):
        self.calls.append(("test", dict(ctx.config), dict(ctx.secrets)))
        if self.error:
            raise self.error
        return "Всё хорошо"

    async def export(self, ctx, doc):
        self.calls.append(("export", doc.id, dict(ctx.secrets)))
        self.docs.append(doc)
        if self.export_delay:
            await asyncio.sleep(self.export_delay)
        if self.export_errors:
            raise self.export_errors.pop(0)
        return self.external_id

    async def notify(self, ctx, doc, purpose):
        self.calls.append(("notify", doc.id, purpose))
        self.docs.append(doc)
        if self.notify_errors:
            raise self.notify_errors.pop(0)


class FakeNotifier(connectors_base.BaseConnector):
    """Умеет только уведомлять (как Telegram/почта): выгрузок для него не создаётся."""

    kind = "test_notifier"
    label = "Тестовые уведомления"

    def __init__(self):
        self.calls = []

    async def notify(self, ctx, doc, purpose):
        self.calls.append(("notify", doc.id, purpose))


@pytest.fixture
def fake_connector():
    fake = FakeConnector()
    connectors_base.REGISTRY["test_fake"] = fake
    yield fake
    connectors_base.REGISTRY.pop("test_fake", None)


@pytest.fixture
def fake_notifier():
    fake = FakeNotifier()
    connectors_base.REGISTRY["test_notifier"] = fake
    yield fake
    connectors_base.REGISTRY.pop("test_notifier", None)
