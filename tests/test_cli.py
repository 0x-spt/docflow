import asyncio
import io
import json

import pytest
from PIL import Image
from sqlalchemy import func, select

from docflow.cli import main
from docflow.config import get_settings
from docflow.db.models import ApiKey, Base, Lead, SchemaRecord, Tenant, hash_key
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


def test_create_tenant_empty_name_exit_1(db_url, capsys):
    assert main(["create-tenant", "   "]) == 1
    assert capsys.readouterr().err.startswith("Ошибка")


def test_eval_bad_expected_and_missing_fake_slug_exit_1(tmp_path, capsys):
    root, fake = _dataset(tmp_path)
    (root / "a" / "expected.json").write_text("{oops", encoding="utf-8")
    assert main(["eval", str(root), "--extractor", "fake", "--fake-responses", str(fake)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("Ошибка") and "a" in err and "Traceback" not in err
    fake.write_text("{}", encoding="utf-8")
    assert main(["eval", str(root), "--extractor", "fake", "--fake-responses", str(fake)]) == 1
    assert "invoice_ru" in capsys.readouterr().err


def _tenant(url):
    async def one(s):
        return (await s.execute(select(Tenant))).scalar_one()

    return _query(url, one)


@pytest.mark.parametrize(
    "args, expected",
    [
        ([], True),  # облачной модели по умолчанию доверяем
        (["--extractor", "ollama"], False),  # локальной — нет, пока эталонный прогон не подтвердит точность
        (["--extractor", "ollama", "--auto-ok", "yes"], True),
        (["--auto-ok", "no"], False),
    ],
)
def test_create_tenant_auto_ok_default(db_url, capsys, args, expected):
    assert main(["create-tenant", "Ромашка", *args]) == 0
    t = _tenant(db_url)
    assert t.auto_ok is expected
    out = capsys.readouterr().out
    assert str(t.id) in out
    assert ("ok ставится автоматически" if expected else "черновик") in out


def test_set_auto_ok(db_url, capsys):
    main(["create-tenant", "Лаборатория", "--extractor", "ollama"])
    t = _tenant(db_url)
    assert main(["set-auto-ok", str(t.id), "yes"]) == 0
    assert _tenant(db_url).auto_ok is True
    assert main(["set-auto-ok", str(t.id), "no"]) == 0
    assert _tenant(db_url).auto_ok is False


def test_set_auto_ok_unknown_tenant_exit_1(db_url, capsys):
    assert main(["set-auto-ok", "00000000-0000-0000-0000-000000000000", "yes"]) == 1
    assert main(["set-auto-ok", "not-a-uuid", "yes"]) == 1


def _make_tenant_id(db_url):
    async def make(s):
        t = Tenant(name="Ромашка")
        s.add(t)
        await s.commit()
        return t.id

    return _query(db_url, make)


def test_create_user_prints_password_once(db_url, capsys):
    from docflow.auth import verify_password
    from docflow.db.models import User

    tid = _make_tenant_id(db_url)
    assert main(["create-user", str(tid), "  Anna@Example.com "]) == 0
    out = capsys.readouterr().out
    password = next(line.split(": ", 1)[1].strip() for line in out.splitlines() if line.startswith("Пароль: "))
    assert len(password) == 16

    u = _query(db_url, lambda s: s.scalar(select(User)))
    assert u.email == "anna@example.com" and u.role == "owner" and u.tenant_id == tid
    assert verify_password(u.password_hash, password)


def test_create_user_explicit_password_and_role(db_url, capsys):
    from docflow.db.models import User

    tid = _make_tenant_id(db_url)
    assert main(["create-user", str(tid), "op@example.com", "--role", "operator", "--password", "long-enough-1"]) == 0
    assert "long-enough-1" not in capsys.readouterr().out
    assert _query(db_url, lambda s: s.scalar(select(User.role))) == "operator"


def test_create_user_short_password_exit_1(db_url, capsys):
    tid = _make_tenant_id(db_url)
    assert main(["create-user", str(tid), "a@example.com", "--password", "short"]) == 1
    assert "10" in capsys.readouterr().err


def test_create_user_duplicate_email_exit_1(db_url, capsys):
    tid = _make_tenant_id(db_url)
    assert main(["create-user", str(tid), "a@example.com"]) == 0
    capsys.readouterr()
    assert main(["create-user", str(tid), "A@example.com"]) == 1
    assert "уже" in capsys.readouterr().err


def test_create_user_unknown_tenant_exit_1(db_url, capsys):
    import uuid

    assert main(["create-user", str(uuid.uuid4()), "a@example.com"]) == 1
    assert "не найден" in capsys.readouterr().err


def test_leads_lists_recent(db_url, capsys):
    async def add(s):
        s.add(Lead(name="Иван Петров", company="ООО Ромашка", contact="@ivan", volume="100-1000"))
        s.add(Lead(name="Анна", contact="anna@example.com", volume="unknown"))
        await s.commit()

    _query(db_url, add)
    assert main(["leads", "--limit", "10"]) == 0
    out = capsys.readouterr().out
    assert "Иван Петров" in out and "ООО Ромашка" in out and "@ivan" in out and "100-1000" in out
    assert "Анна" in out and "anna@example.com" in out


def test_leads_limit(db_url, capsys):
    async def add(s):
        for i in range(3):
            s.add(Lead(name=f"Клиент{i}", contact="12345", volume="unknown"))
        await s.commit()

    _query(db_url, add)
    assert main(["leads", "--limit", "2"]) == 0
    assert capsys.readouterr().out.count("Клиент") == 2


def test_leads_shows_targets(db_url, capsys):
    async def add(s):
        s.add(Lead(name="Иван", contact="@ivan", volume="unknown", targets=["bitrix24", "own_system"]))
        s.add(Lead(name="Анна", contact="anna@example.com", volume="unknown"))
        await s.commit()

    _query(db_url, add)
    assert main(["leads"]) == 0
    out = capsys.readouterr().out
    assert "Битрикс24, Своя система" in out
