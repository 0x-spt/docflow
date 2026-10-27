"""Сборка приложения FastAPI.

Прод: `uvicorn --factory docflow.api.app:create_app` — движок БД, хранилище и пул arq создаёт lifespan.
Тесты передают свои sessionmaker, хранилище и очередь; lifespan тогда ничего не создаёт.
"""

import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from docflow.api import documents, schemas, webhooks
from docflow.config import Settings, get_settings
from docflow.db.session import make_engine, make_sessionmaker
from docflow.pipeline import SchemaResolver
from docflow.queue import ArqEnqueuer, Enqueuer
from docflow.storage.blobs import BlobStore, blob_store_from_settings

logger = logging.getLogger(__name__)

DESCRIPTION = (
    "Распознавание документов: загрузите скан или фото, получите PDF с текстовым слоем и данные по схеме "
    "документа. Авторизация — заголовок `Authorization: Bearer dfk_...`."
)


@asynccontextmanager
async def _lifespan(app: FastAPI):
    state = app.state
    settings: Settings = state.settings
    engine = pool = None
    try:
        if state.sessionmaker is None:
            engine = make_engine(settings.database_url)
            state.sessionmaker = make_sessionmaker(engine)
        if state.blobs is None:
            state.blobs = blob_store_from_settings(settings)
        if state.enqueuer is None:
            pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
            state.enqueuer = ArqEnqueuer(pool)
        yield
    finally:
        if pool is not None:
            await pool.aclose()
        if engine is not None:
            await engine.dispose()


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("необработанная ошибка в %s %s", request.method, request.url.path, exc_info=exc)
    return JSONResponse({"detail": "внутренняя ошибка сервера"}, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)


def create_app(
    settings: Settings | None = None,
    *,
    sessionmaker: async_sessionmaker | None = None,
    blobs: BlobStore | None = None,
    enqueuer: Enqueuer | None = None,
) -> FastAPI:
    app = FastAPI(title="DocFlow API", version="0.1.0", description=DESCRIPTION, lifespan=_lifespan)
    app.state.settings = settings or get_settings()
    app.state.sessionmaker = sessionmaker
    app.state.blobs = blobs
    app.state.enqueuer = enqueuer
    app.state.schemas = SchemaResolver()
    app.add_exception_handler(Exception, _unhandled)

    @app.get("/healthz", tags=["service"], summary="Проверка живости", description="Отвечает, пока процесс жив.")
    async def healthz() -> dict:
        return {"status": "ok"}

    app.include_router(documents.router)
    app.include_router(schemas.router)
    app.include_router(webhooks.router)
    return app
