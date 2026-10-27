import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

from docflow.config import Settings
from docflow.db.models import Base

ROOT = Path(__file__).resolve().parents[2]


def _config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


def test_migration_matches_models_on_sqlite(tmp_path):
    db = tmp_path / "m.db"
    command.upgrade(_config(f"sqlite+aiosqlite:///{db.as_posix()}"), "head")

    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    engine.dispose()
    # JSON с вариантом JSONB на SQLite сравнивается как JSON; других расхождений быть не должно
    assert diff == []


def test_downgrade_removes_tables(tmp_path):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'd.db').as_posix()}"
    cfg = _config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(url.replace("+aiosqlite", ""))
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}
    engine.dispose()


def _postgres_reachable(url: str) -> bool:
    async def probe() -> None:
        engine = create_async_engine(url)
        try:
            async with engine.connect():
                pass
        finally:
            await engine.dispose()

    try:
        asyncio.run(asyncio.wait_for(probe(), 3))
        return True
    except Exception:
        return False


def _test_db_url(url: str) -> str:
    return make_url(url).set(database="docflow_test").render_as_string(hide_password=False)


async def _ensure_database(url: str) -> None:
    # отдельная база, чтобы downgrade не трогал dev-данные; CREATE DATABASE нельзя внутри транзакции
    admin = create_async_engine(make_url(url).set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            exists = await conn.scalar(text("SELECT 1 FROM pg_database WHERE datname = 'docflow_test'"))
            if not exists:
                await conn.execute(text("CREATE DATABASE docflow_test"))
    finally:
        await admin.dispose()


@pytest.mark.integration
def test_migration_upgrade():
    base_url = Settings().database_url
    if not base_url.startswith("postgresql") or not _postgres_reachable(base_url):
        pytest.skip("Postgres недоступен (docker compose up -d postgres)")
    asyncio.run(_ensure_database(base_url))
    url = _test_db_url(base_url)
    cfg = _config(url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")

    async def check():
        engine = create_async_engine(url)
        async with engine.connect() as conn:
            diff = await conn.run_sync(
                lambda c: compare_metadata(MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata)
            )
        await engine.dispose()
        return diff

    assert asyncio.run(check()) == []
