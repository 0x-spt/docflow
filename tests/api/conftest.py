from dataclasses import dataclass

import httpx
import pytest
from api_helpers import BASE_URL
from cryptography.fernet import Fernet
from sqlalchemy import event

from docflow.api.app import create_app
from docflow.config import Settings
from docflow.connectors import base as connectors_base
from docflow.db.models import ApiKey, Base, Tenant, generate_api_key
from docflow.db.session import make_engine, make_sessionmaker
from docflow.queue import ListEnqueuer
from docflow.storage.blobs import LocalBlobStore


@dataclass
class Api:
    client: httpx.AsyncClient
    sessionmaker: object
    blobs: LocalBlobStore
    enqueuer: ListEnqueuer
    settings: Settings


@pytest.fixture
async def api(tmp_path):
    engine = make_engine(f"sqlite+aiosqlite:///{(tmp_path / 'db.sqlite').as_posix()}")

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _record):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = make_sessionmaker(engine)
    blobs = LocalBlobStore(tmp_path / "blobs")
    enqueuer = ListEnqueuer()
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        public_base_url=BASE_URL,
        max_file_mb=20,
        max_pages=30,
        webhook_allow_private=False,
        secrets_key=Fernet.generate_key().decode(),
    )
    app = create_app(settings, sessionmaker=sm, blobs=blobs, enqueuer=enqueuer)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield Api(client, sm, blobs, enqueuer, settings)
    await engine.dispose()


async def _new_tenant(sm, name: str) -> dict[str, str]:
    async with sm() as s:
        tenant = Tenant(name=name)
        s.add(tenant)
        await s.flush()
        plain, prefix, key_hash = generate_api_key()
        s.add(ApiKey(tenant_id=tenant.id, prefix=prefix, key_hash=key_hash))
        await s.commit()
    return {"Authorization": f"Bearer {plain}"}


@pytest.fixture
def make_tenant(api):
    """Фабрика: новый тенант с ключом API → заголовки авторизации."""

    async def _make(name: str = "Ромашка") -> dict[str, str]:
        return await _new_tenant(api.sessionmaker, name)

    return _make


@pytest.fixture
async def auth(make_tenant):
    return await make_tenant()


class FakeConnector(connectors_base.BaseConnector):
    """Фиктивный получатель для тестов API: помнит вызовы, ошибку можно подложить."""

    kind = "test_fake"
    label = "Тестовый"
    config_fields = [
        connectors_base.FieldDef("endpoint", "Адрес", required=True, type="url"),
        connectors_base.FieldDef("note", "Заметка"),
    ]
    secret_fields = [connectors_base.FieldDef("token", "Токен", required=True)]
    target_fields = [connectors_base.FieldDef("amount", "Сумма")]

    def __init__(self):
        self.calls = []
        self.error: Exception | None = None

    async def test(self, ctx):
        self.calls.append(("test", dict(ctx.config), dict(ctx.secrets)))
        if self.error:
            raise self.error
        return "Всё хорошо"


@pytest.fixture
def fake_connector():
    fake = FakeConnector()
    connectors_base.REGISTRY["test_fake"] = fake
    yield fake
    connectors_base.REGISTRY.pop("test_fake", None)
