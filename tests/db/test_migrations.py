import asyncio
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect
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


@pytest.mark.integration
def test_migration_upgrade():
    url = Settings().database_url
    if not url.startswith("postgresql") or not _postgres_reachable(url):
        pytest.skip("Postgres недоступен (docker compose up -d postgres)")
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
