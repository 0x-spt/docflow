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
