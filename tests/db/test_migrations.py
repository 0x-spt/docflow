import asyncio
import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine

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
    # только явно заданный адрес: адрес по умолчанию из настроек указывает на чужой Postgres на этой машине
    base_url = os.environ.get("DOCFLOW_DATABASE_URL")
    if not base_url:
        pytest.skip("задайте DOCFLOW_DATABASE_URL (compose: postgresql+asyncpg://…@127.0.0.1:55432/docflow)")
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


def test_integration_skips_without_explicit_url(monkeypatch):
    def must_not_connect(url):
        raise AssertionError("без явного адреса подключения быть не должно")

    monkeypatch.delenv("DOCFLOW_DATABASE_URL", raising=False)
    monkeypatch.setitem(globals(), "_postgres_reachable", must_not_connect)
    with pytest.raises(pytest.skip.Exception):
        test_migration_upgrade()


def _sqlite_engine(tmp_path, name):
    return create_engine(f"sqlite:///{(tmp_path / name).as_posix()}")


def test_existing_tenants_unlimited_after_upgrade(tmp_path):
    db = tmp_path / "u.db"
    cfg = _config(f"sqlite+aiosqlite:///{db.as_posix()}")
    command.upgrade(cfg, "0010")
    engine = _sqlite_engine(tmp_path, "u.db")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id, name, extractor, auto_ok, created_at)"
                " VALUES ('t1', 'old', 'claude', 1, '2026-01-01')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, tenant_id, schema_slug, schema_version, status, files, mode, input_tokens,"
                " output_tokens, pages, created_at)"
                " VALUES ('d1', 't1', 'invoice', 1, 'ok', '[]', 'fast', 0, 0, 1, '2026-01-01')"
            )
        )
    command.upgrade(cfg, "head")
    with engine.connect() as conn:
        plan, limit = conn.execute(text("SELECT plan, pages_limit FROM tenants")).one()
        source = conn.scalar(text("SELECT source FROM documents"))
    engine.dispose()
    assert plan == "unlimited"
    assert limit is None
    assert source == "api"


def test_downgrade_0011_drops_bot_exports(tmp_path):
    db = tmp_path / "x.db"
    cfg = _config(f"sqlite+aiosqlite:///{db.as_posix()}")
    command.upgrade(cfg, "head")
    engine = _sqlite_engine(tmp_path, "x.db")
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO tenants (id, name, extractor, auto_ok, created_at, plan)"
                " VALUES ('t1', 'n', 'claude', 1, '2026-01-01', 'trial')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO documents (id, tenant_id, schema_slug, schema_version, status, files, mode, input_tokens,"
                " output_tokens, pages, created_at, source)"
                " VALUES ('d1', 't1', 'invoice', 1, 'ok', '[]', 'fast', 0, 0, 1, '2026-01-01', 'api')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO exports (id, tenant_id, document_id, purpose, status, attempt, created_at, updated_at)"
                " VALUES ('e1', 't1', 'd1', 'bot_ready', 'pending', 0, '2026-01-01', '2026-01-01')"
            )
        )
    command.downgrade(cfg, "0010")
    with engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM exports")) == 0
    engine.dispose()
