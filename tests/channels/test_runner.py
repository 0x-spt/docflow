import asyncio
import logging

import pytest
from sqlalchemy import update

from docflow.channels import runner
from docflow.channels.deps import ChannelDeps
from docflow.channels.runner import BotSupervisor, mail_pass
from docflow.config import Settings
from docflow.db.models import Mailbox, Tenant, TenantBot


class FakePoller:
    """run() висит до отмены; crash=True — падает сразу."""

    def __init__(self, bot_id, crash=False):
        self.bot_id = bot_id
        self.crash = crash

    async def run(self):
        if self.crash:
            raise RuntimeError("secret-token-text")
        await asyncio.Event().wait()


class Env:
    def __init__(self, sm):
        self.sm = sm
        self.deps = ChannelDeps(sessionmaker=sm, intake=None, settings=Settings(database_url="sqlite+aiosqlite://"))
        self.created: list[FakePoller] = []
        self.crash: set = set()
        self.sup = BotSupervisor(self.deps, None, poller_factory=self._factory)

    def _factory(self, deps, bot_id, http):
        poller = FakePoller(bot_id, crash=bot_id in self.crash)
        self.created.append(poller)
        return poller

    async def add_bot(self, n=0, active=True):
        async with self.sm() as s:
            tenant = Tenant(name=f"T{n}")
            s.add(tenant)
            await s.flush()
            bot = TenantBot(
                tenant_id=tenant.id,
                token_enc="x",
                token_hash=f"h{n}",
                username=f"bot{n}",
                invite_code=f"c{n}",
                active=active,
            )
            s.add(bot)
            await s.commit()
            return bot.id

    async def set_active(self, bot_id, active):
        async with self.sm() as s:
            await s.execute(update(TenantBot).where(TenantBot.id == bot_id).values(active=active))
            await s.commit()

    async def delete_bot(self, bot_id):
        async with self.sm() as s:
            await s.delete(await s.get(TenantBot, bot_id))
            await s.commit()


@pytest.fixture
async def env(sm):
    e = Env(sm)
    yield e
    await e.sup.stop()


async def test_refresh_starts_active_bots(env):
    a = await env.add_bot(1)
    await env.add_bot(2, active=False)
    await env.sup.refresh()
    await env.sup.refresh()
    assert [p.bot_id for p in env.created] == [a]


async def test_refresh_stops_disconnected_or_inactive_bot(env):
    a = await env.add_bot(1)
    b = await env.add_bot(2)
    await env.sup.refresh()
    tasks = dict(env.sup._tasks)
    await env.set_active(a, False)
    await env.delete_bot(b)
    await env.sup.refresh()
    assert tasks[a].cancelled() and tasks[b].cancelled()
    assert env.sup._tasks == {}


async def test_refresh_starts_new_bot_after_reconnect(env):
    a = await env.add_bot(1)
    await env.sup.refresh()
    await env.delete_bot(a)
    await env.sup.refresh()
    c = await env.add_bot(1)
    await env.sup.refresh()
    assert [p.bot_id for p in env.created] == [a, c]


async def test_crashed_poller_restarted_on_refresh(env, caplog):
    a = await env.add_bot(1)
    env.crash.add(a)
    await env.sup.refresh()
    await asyncio.sleep(0.01)
    env.crash.clear()
    with caplog.at_level(logging.WARNING):
        await env.sup.refresh()
    assert [p.crash for p in env.created] == [True, False]
    assert "RuntimeError" in caplog.text and "secret-token-text" not in caplog.text


async def test_finished_poller_of_inactive_bot_not_restarted(env):
    a = await env.add_bot(1)
    env.crash.add(a)  # run() завершилась; в БД бот уже active=False (токен отозван)
    await env.sup.refresh()
    await asyncio.sleep(0.01)
    await env.set_active(a, False)
    await env.sup.refresh()
    assert len(env.created) == 1 and env.sup._tasks == {}


async def test_one_bot_crash_does_not_touch_other(env):
    a = await env.add_bot(1)
    b = await env.add_bot(2)
    env.crash.add(a)
    await env.sup.refresh()
    await asyncio.sleep(0.01)
    assert not env.sup._tasks[b].done()


async def test_refresh_survives_db_failure(env, caplog):
    await env.add_bot(1)
    await env.sup.refresh()

    def broken():
        raise ConnectionError("db password leaked")

    env.deps.sessionmaker = broken
    with caplog.at_level(logging.WARNING):
        await env.sup.refresh()
    assert "ConnectionError" in caplog.text and "leaked" not in caplog.text
    assert len(env.sup._tasks) == 1


class FakeMailPoller:
    def __init__(self, fail=()):
        self.fail = set(fail)
        self.polled = []

    async def poll(self, mailbox_id):
        self.polled.append(mailbox_id)
        if mailbox_id in self.fail:
            raise RuntimeError("imap password leaked")


async def _boxes(sm, *states):
    ids = []
    async with sm() as s:
        for n, active in enumerate(states):
            tenant = Tenant(name=f"M{n}")
            s.add(tenant)
            await s.flush()
            box = Mailbox(
                tenant_id=tenant.id, host="h", port=993, username="u", password_enc="x", folder="INBOX", active=active
            )
            s.add(box)
            await s.flush()
            ids.append(box.id)
        await s.commit()
    return ids


async def test_mail_pass_skips_inactive(sm):
    a, _ = await _boxes(sm, True, False)
    poller = FakeMailPoller()
    await mail_pass(ChannelDeps(sm, None, None), poller)
    assert poller.polled == [a]


async def test_mail_pass_one_failure_continues(sm, caplog):
    a, b = await _boxes(sm, True, True)
    poller = FakeMailPoller(fail=[a])
    with caplog.at_level(logging.WARNING):
        await mail_pass(ChannelDeps(sm, None, None), poller)
    assert sorted(poller.polled) == sorted([a, b])
    assert "RuntimeError" in caplog.text and "leaked" not in caplog.text


async def test_mail_pass_survives_db_failure(caplog):
    def broken():
        raise ConnectionError("leaked")

    poller = FakeMailPoller()
    with caplog.at_level(logging.WARNING):
        await mail_pass(ChannelDeps(broken, None, None), poller)
    assert poller.polled == [] and "leaked" not in caplog.text


def test_intervals():
    assert runner.BOT_REFRESH == 5.0 and runner.MAIL_INTERVAL == 60.0
