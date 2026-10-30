from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


def make_engine(url: str) -> AsyncEngine:
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database in (None, "", ":memory:"):
        # in-memory SQLite живёт в одном соединении — иначе каждое соединение видит пустую базу
        return create_async_engine(
            url, poolclass=StaticPool, connect_args={"check_same_thread": False}, hide_parameters=True
        )
    # параметры запроса в тексте ошибки БД содержали бы зашифрованные токены и хеши
    return create_async_engine(url, hide_parameters=True)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)
