"""Сборка приложения FastAPI.

Прод: `uvicorn --factory docflow.api.app:create_app` — движок БД, хранилище и пул arq создаёт lifespan.
Тесты передают свои sessionmaker, хранилище и очередь; lifespan тогда ничего не создаёт.
"""

import logging
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import async_sessionmaker

from docflow.api import (
    apikeys,
    auth,
    bot,
    destinations,
    dictionaries,
    documents,
    exports,
    leads,
    mailbox,
    routing,
    schemas,
    stats,
    usage,
    webhooks,
)
from docflow.api.limits import RequestSizeLimit
from docflow.api.ratelimit import SlidingWindowLimiter
from docflow.channels.mailbox import open_imap
from docflow.config import Settings, get_settings
from docflow.db.session import make_engine, make_sessionmaker
from docflow.pipeline import SchemaResolver
from docflow.queue import ArqEnqueuer, Enqueuer
from docflow.schemas.model import SchemaPathError
from docflow.storage.blobs import BlobStore, blob_store_from_settings

logger = logging.getLogger(__name__)

# маршруты, в теле которых бывает секрет (токен бота, пароль ящика)
SECRET_BODY_PREFIXES = ("/v1/bot", "/v1/mailbox")

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


def _carries_secret(path: str) -> bool:
    return any(path == prefix or path.startswith(f"{prefix}/") for prefix in SECRET_BODY_PREFIXES)


async def _validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 как обычно, но ошибка схемы указывает на поле или правило: к loc дописывается путь SchemaPathError."""
    errors = []
    for err in exc.errors():
        cause = (err.get("ctx") or {}).get("error")
        if isinstance(cause, SchemaPathError):
            err = {**err, "loc": (*err["loc"], *cause.loc)}
        if _carries_secret(request.url.path):
            # стандартный 422 возвращает принятое значение в input, а лишний ключ тела попадает в loc:
            # в теле запроса может быть секрет, поэтому от пути оставляем только «body»/«query»
            err = {**{k: v for k, v in err.items() if k not in ("input", "ctx")}, "loc": err["loc"][:1]}
        errors.append(err)
    return await request_validation_exception_handler(request, RequestValidationError(errors, body=exc.body))


def create_app(
    settings: Settings | None = None,
    *,
    sessionmaker: async_sessionmaker | None = None,
    blobs: BlobStore | None = None,
    enqueuer: Enqueuer | None = None,
) -> FastAPI:
    # httpx на INFO пишет URL запросов целиком, а в Bot API токен бота лежит в пути
    logging.getLogger("httpx").setLevel(logging.WARNING)
    app = FastAPI(title="DocFlow API", version="0.1.0", description=DESCRIPTION, lifespan=_lifespan)
    app.state.settings = settings or get_settings()
    app.state.sessionmaker = sessionmaker
    app.state.blobs = blobs
    app.state.enqueuer = enqueuer
    app.state.schemas = SchemaResolver()
    # транспорт httpx для запросов к Bot API; None — настоящая сеть, тесты подставляют MockTransport
    app.state.bot_transport = None
    # подключение к IMAP; тесты подставляют заглушку
    app.state.imap_connect = open_imap
    # попытки входа считаем в памяти процесса; у каждого приложения свои счётчики
    app.state.login_email_limiter = SlidingWindowLimiter(limit=5, window_s=900)
    app.state.login_ip_limiter = SlidingWindowLimiter(limit=20, window_s=900)
    # заявки с сайта: 5 в час с одного адреса
    app.state.leads_limiter = SlidingWindowLimiter(limit=5, window_s=3600)
    app.add_exception_handler(Exception, _unhandled)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_middleware(RequestSizeLimit)

    @app.get("/healthz", tags=["service"], summary="Проверка живости", description="Отвечает, пока процесс жив.")
    async def healthz() -> dict:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(leads.router)
    app.include_router(apikeys.router)
    app.include_router(documents.router)
    app.include_router(stats.router)
    app.include_router(usage.router)
    app.include_router(schemas.router)
    app.include_router(webhooks.router)
    app.include_router(bot.router)
    app.include_router(mailbox.router)
    app.include_router(destinations.router)
    app.include_router(destinations.catalog_router)
    app.include_router(routing.router)
    app.include_router(exports.router)
    app.include_router(dictionaries.router)
    return app
