import asyncio
import io
import json

import pytest
from PIL import Image
from sqlalchemy import func, select

from docflow.cli import main
from docflow.config import get_settings
from docflow.db.models import ApiKey, Base, SchemaRecord, Tenant, hash_key
from docflow.db.session import make_engine, make_sessionmaker
from docflow.schemas.library import builtin_schemas


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{(tmp_path / 'cli.db').as_posix()}"
    monkeypatch.setenv("DOCFLOW_DATABASE_URL", url)
    get_settings.cache_clear()

    async def create():
        engine = make_engine(url)
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        await engine.dispose()

    asyncio.run(create())
    yield url
    get_settings.cache_clear()


def _query(url, fn):
    async def go():
        engine = make_engine(url)
        try:
            async with make_sessionmaker(engine)() as s:
                return await fn(s)
        finally:
            await engine.dispose()

    return asyncio.run(go())


def test_create_tenant_prints_key_once(db_url, capsys):
    assert main(["create-tenant", "Ромашка", "--extractor", "ollama"]) == 0
    out = capsys.readouterr().out
    key = next(w for w in out.split() if w.startswith("dfk_"))
    assert "больше не будет показан" in out

    async def rows(s):
        t = (await s.execute(select(Tenant))).scalar_one()
        k = (await s.execute(select(ApiKey))).scalar_one()
        return t, k

    t, k = _query(db_url, rows)
    assert t.name == "Ромашка" and t.extractor == "ollama"
    assert k.tenant_id == t.id and k.key_hash == hash_key(key) and k.prefix == key[:8]


def test_seed_schemas_is_idempotent(db_url, capsys):
    n = len(builtin_schemas())
    assert main(["seed-schemas"]) == 0
    assert f"добавлено: {n}" in capsys.readouterr().out
    assert main(["seed-schemas"]) == 0
    assert "добавлено: 0" in capsys.readouterr().out

    async def count(s):
        q = select(func.count()).select_from(SchemaRecord).where(SchemaRecord.tenant_id.is_(None))
        return (await s.execute(q)).scalar_one()

    assert _query(db_url, count) == n


def test_db_error_is_russian_message_exit_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("DOCFLOW_DATABASE_URL", f"sqlite+aiosqlite:///{(tmp_path / 'nodir' / 'x.db').as_posix()}")
    get_settings.cache_clear()
    try:
        assert main(["seed-schemas"]) == 1
    finally:
        get_settings.cache_clear()
    err = capsys.readouterr().err
    assert err.startswith("Ошибка") and "Traceback" not in err


def _dataset(tmp_path):
    root = tmp_path / "invoice_ru"
    case = root / "a"
    case.mkdir(parents=True)
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), "white").save(buf, format="JPEG")
    (case / "1.jpg").write_bytes(buf.getvalue())
    (case / "expected.json").write_text(json.dumps({"number": "15", "total": 100}), encoding="utf-8")
    fake = tmp_path / "fake.json"
    fake.write_text(json.dumps({"invoice_ru": {
        "number": "15", "issued": "2026-01-10", "total": 100, "subtotal": 100,
        "items": [{"name": "Болт", "qty": 1, "unit": "шт", "price": 100, "amount": 100}]}}), encoding="utf-8")
    return root, fake


def test_eval_fake_writes_report(tmp_path, capsys):
    root, fake = _dataset(tmp_path)
    assert main(["eval", str(root), "--extractor", "fake", "--fake-responses", str(fake)]) == 0
    data = json.loads((root.parent / "eval-report.json").read_text(encoding="utf-8"))
    assert data["ok_precision"] == 1.0 and data["overall_accuracy"] == 1.0
    assert "ok_precision" in capsys.readouterr().out

    out = tmp_path / "custom.json"
    assert main(["eval", str(root), "--extractor", "fake", "--fake-responses", str(fake), "--out", str(out)]) == 0
    assert out.exists()


def test_eval_user_errors(tmp_path, capsys):
    assert main(["eval", str(tmp_path / "missing"), "--extractor", "fake"]) == 1
    assert capsys.readouterr().err.startswith("Ошибка")
    bad = tmp_path / "unknown_slug"
    bad.mkdir()
    assert main(["eval", str(bad), "--extractor", "fake"]) == 1
