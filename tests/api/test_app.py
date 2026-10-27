from docflow.api import app as app_module
from docflow.config import Settings
from docflow.queue import ArqEnqueuer
from docflow.storage.blobs import LocalBlobStore


class FakePool:
    closed = False

    async def aclose(self):
        self.closed = True


async def test_lifespan_builds_and_closes_resources(tmp_path, monkeypatch):
    pool = FakePool()
    seen = {}

    async def fake_create_pool(redis_settings):
        seen["host"] = redis_settings.host
        return pool

    monkeypatch.setattr(app_module, "create_pool", fake_create_pool)
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:", redis_url="redis://queue-host:6379/0", blob_root=tmp_path
    )
    app = app_module.create_app(settings)
    async with app.router.lifespan_context(app):
        assert isinstance(app.state.enqueuer, ArqEnqueuer)
        assert isinstance(app.state.blobs, LocalBlobStore)
        assert app.state.sessionmaker is not None
    assert seen["host"] == "queue-host"
    assert pool.closed
