"""Процесс `docflow channels`: опрос ботов Telegram и почтовых ящиков клиентов."""

import asyncio
import contextlib
import logging
import signal
import tempfile
from pathlib import Path
from uuid import UUID

import httpx
from sqlalchemy import select

from docflow.channels.bot_poller import BotPoller
from docflow.channels.deps import ChannelDeps
from docflow.channels.mailbox import MailboxPoller
from docflow.config import Settings
from docflow.db.models import Mailbox, TenantBot

logger = logging.getLogger(__name__)

BOT_REFRESH = 5.0  # как часто сверять список ботов с БД
MAIL_INTERVAL = 60.0  # как часто обходить ящики
# файл обновляется каждым тиком цикла ботов; healthcheck контейнера проверяет, что он свежий
HEARTBEAT = Path(tempfile.gettempdir()) / "docflow-channels-alive"


class BotSupervisor:
    """Держит по одной задаче-поллеру на каждого активного бота."""

    def __init__(self, deps: ChannelDeps, http: httpx.AsyncClient, poller_factory=BotPoller):
        self._deps = deps
        self._http = http
        self._factory = poller_factory
        self._tasks: dict[UUID, asyncio.Task] = {}

    async def refresh(self) -> None:
        try:
            async with self._deps.sessionmaker() as session:
                active = set((await session.execute(select(TenantBot.id).where(TenantBot.active.is_(True)))).scalars())
        except Exception as exc:
            logger.warning("список ботов не прочитан: %s", type(exc).__name__)
            return
        for bot_id in list(self._tasks):
            task = self._tasks[bot_id]
            if task.done():
                # завершилась сама: токен отозван (тогда бот уже не active) либо поллер упал и поднимется ниже
                del self._tasks[bot_id]
                if not task.cancelled() and task.exception() is not None:
                    logger.warning("бот %s: поллер упал: %s", bot_id, type(task.exception()).__name__)
            elif bot_id not in active:
                await self._cancel(task)
                del self._tasks[bot_id]
        for bot_id in active - self._tasks.keys():
            poller = self._factory(self._deps, bot_id, self._http)
            self._tasks[bot_id] = asyncio.create_task(poller.run())

    async def stop(self) -> None:
        tasks = list(self._tasks.values())
        self._tasks.clear()
        for task in tasks:
            await self._cancel(task)

    @staticmethod
    async def _cancel(task: asyncio.Task) -> None:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def mail_pass(deps: ChannelDeps, poller: MailboxPoller) -> None:
    """Обходит активные ящики по очереди; сбой одного (или БД) не мешает остальным и следующему проходу."""
    try:
        async with deps.sessionmaker() as session:
            ids = list((await session.execute(select(Mailbox.id).where(Mailbox.active.is_(True)))).scalars())
    except Exception as exc:
        logger.warning("список ящиков не прочитан: %s", type(exc).__name__)
        return
    for mailbox_id in ids:
        try:
            await poller.poll(mailbox_id)
        except Exception as exc:  # текст исключения может содержать адрес сервера или данные входа
            logger.warning("ящик %s: сбой прохода: %s", mailbox_id, type(exc).__name__)


async def _every(interval: float, stop: asyncio.Event, work) -> None:
    while not stop.is_set():
        try:
            await work()
        except Exception as exc:  # один сбой не должен останавливать процесс
            logger.warning("сбой цикла каналов: %s", type(exc).__name__)
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), interval)


async def _bot_tick(supervisor: BotSupervisor) -> None:
    try:
        await supervisor.refresh()
    finally:
        try:
            HEARTBEAT.touch()
        except OSError:
            pass


async def run_channels(settings: Settings) -> None:
    from arq import create_pool
    from arq.connections import RedisSettings

    from docflow.db.session import make_engine, make_sessionmaker
    from docflow.intake import IntakeDeps
    from docflow.pipeline import SchemaResolver
    from docflow.queue import ArqEnqueuer
    from docflow.storage.blobs import blob_store_from_settings

    logging.getLogger("httpx").setLevel(logging.WARNING)  # URL с токеном бота не должны попадать в INFO-логи
    engine = make_engine(settings.database_url)
    pool = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    http = httpx.AsyncClient(timeout=httpx.Timeout(45, connect=10))
    try:
        intake = IntakeDeps(
            settings=settings,
            blobs=blob_store_from_settings(settings),
            enqueuer=ArqEnqueuer(pool),
            resolver=SchemaResolver(),
        )
        deps = ChannelDeps(sessionmaker=make_sessionmaker(engine), intake=intake, settings=settings)
        supervisor = BotSupervisor(deps, http)
        mailboxes = MailboxPoller(deps)
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            with contextlib.suppress(NotImplementedError):  # на Windows обработчиков сигналов в цикле нет
                loop.add_signal_handler(sig, stop.set)
        try:
            await asyncio.gather(
                _every(BOT_REFRESH, stop, lambda: _bot_tick(supervisor)),
                _every(MAIL_INTERVAL, stop, lambda: mail_pass(deps, mailboxes)),
            )
        finally:
            await supervisor.stop()
    finally:
        await http.aclose()
        await pool.aclose()
        await engine.dispose()
