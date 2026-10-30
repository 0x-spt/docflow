import logging
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import event, func, select, update

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "api"))
from api_helpers import pdf_bytes, png_bytes  # noqa: E402
from fake_telegram import FakeTelegram  # noqa: E402

from docflow.channels import bot_poller  # noqa: E402
from docflow.channels.bot_poller import ALBUM_TICK, ALBUM_WAIT, CONFLICT_PAUSE, DOWNLOAD_RETRIES, BotPoller  # noqa: E402
from docflow.channels.deps import ChannelDeps  # noqa: E402
from docflow.config import Settings  # noqa: E402
from docflow.db.models import BotChat, Document, Export, Tenant, TenantBot  # noqa: E402
from docflow.intake import IntakeDeps  # noqa: E402
from docflow.pipeline import SchemaResolver  # noqa: E402
from docflow.queue import ListEnqueuer  # noqa: E402
from docflow.secrets import encrypt_secrets  # noqa: E402
from docflow.storage.blobs import LocalBlobStore  # noqa: E402

TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
KEY = Fernet.generate_key().decode()
CODE = "invite-code-1"
CHAT = 500
GROUP = -700
REFUSED = "Попросите ссылку у администратора кабинета"
HINT = "Пришлите фото или PDF документа"
ACCEPTED_ONE = "Принял, распознаю (1 стр.)"
DOWNLOAD_FAILED = "Не удалось скачать файл. Пришлите его ещё раз"


class Env:
    def __init__(self, sm, tmp_path):
        self.sm = sm
        self.tg = FakeTelegram("my_bot")
        self.now = 1000.0
        self.http = httpx.AsyncClient(transport=self.tg.transport)
        self.settings = Settings(database_url="sqlite+aiosqlite:///:memory:", secrets_key=KEY, max_file_mb=20)
        intake = IntakeDeps(
            settings=self.settings,
            blobs=LocalBlobStore(tmp_path / "blobs"),
            enqueuer=ListEnqueuer(),
            resolver=SchemaResolver(),
        )
        self.deps = ChannelDeps(sessionmaker=sm, intake=intake, settings=self.settings)
        self.bot_id = None
        self.tenant_id = None
        self._next_update = 100
        self._next_message = 1

    def poller(self) -> BotPoller:
        return BotPoller(self.deps, self.bot_id, self.http, clock=lambda: self.now)

    def push(self, **fields) -> None:
        """Кладёт сообщение в очередь Telegram; update_id идут подряд с 100."""
        message_id = fields.pop("message_id", None)
        if message_id is None:
            message_id = self._next_message
            self._next_message += 1
        chat = fields.pop("chat", {"id": CHAT, "type": "private", "first_name": "Иван"})
        message = {"message_id": message_id, "chat": chat, **fields}
        self.tg.updates.append({"update_id": self._next_update, "message": message})
        self._next_update += 1

    def group(self, **fields) -> None:
        self.push(chat={"id": GROUP, "type": "group", "title": "Бухгалтерия"}, **fields)

    def photo(self, file_id: str, content: bytes | None = None, **fields) -> None:
        self.tg.files[file_id] = content if content is not None else png_bytes()
        self.push(photo=[{"file_id": file_id, "width": 90, "height": 90, "file_size": 100}], **fields)

    def pdf(self, file_id: str = "pdf1", pages: int = 2, **fields) -> None:
        self.tg.files[file_id] = pdf_bytes(pages)
        document = {"file_id": file_id, "file_name": "scan.pdf", "mime_type": "application/pdf", "file_size": 1000}
        self.push(document=document, **fields)

    def album_part(self, file_id: str, message_id: int, caption: str | None = None) -> None:
        fields = {"caption": caption} if caption else {}
        content = png_bytes((10 + message_id, 10))
        self.photo(file_id, content, message_id=message_id, media_group_id="g1", **fields)

    def methods(self, name: str) -> list:
        return [payload for method, payload in self.tg.calls if method == name]

    def replies(self) -> list[str]:
        return [payload["text"] for payload in self.methods("sendMessage")]

    async def link(self, chat_id: int = CHAT) -> None:
        async with self.sm() as s:
            s.add(BotChat(bot_id=self.bot_id, chat_id=chat_id, title="чат"))
            await s.commit()

    async def docs(self) -> list[Document]:
        async with self.sm() as s:
            return list((await s.execute(select(Document).order_by(Document.created_at))).scalars())

    async def chats(self) -> list[BotChat]:
        async with self.sm() as s:
            return list((await s.execute(select(BotChat))).scalars())

    async def bot(self) -> TenantBot:
        async with self.sm() as s:
            return await s.get(TenantBot, self.bot_id)

    async def set_bot(self, **values) -> None:
        async with self.sm() as s:
            await s.execute(update(TenantBot).where(TenantBot.id == self.bot_id).values(**values))
            await s.commit()


@pytest.fixture
async def env(sm, tmp_path):
    e = Env(sm, tmp_path)
    async with sm() as s:
        tenant = Tenant(name="Ромашка")
        s.add(tenant)
        await s.flush()
        bot = TenantBot(
            tenant_id=tenant.id,
            token_enc=encrypt_secrets({"token": TOKEN}, KEY),
            token_hash="hash",
            username="my_bot",
            invite_code=CODE,
        )
        s.add(bot)
        await s.commit()
        e.tenant_id, e.bot_id = tenant.id, bot.id
    yield e
    await e.http.aclose()


# --- привязка ---------------------------------------------------------------


async def test_start_with_code_links_chat(env):
    env.push(text=f"/start {CODE}")
    await env.poller().poll_once()
    chats = await env.chats()
    assert [c.chat_id for c in chats] == [CHAT]
    assert chats[0].title == "Иван"
    assert env.replies() == ["Готово, присылайте документы. Компания: «Ромашка»"]


async def test_group_start_with_mention_links(env):
    env.group(text=f"/start@my_bot {CODE}")
    await env.poller().poll_once()
    assert [(c.chat_id, c.title) for c in await env.chats()] == [(GROUP, "Бухгалтерия")]
    assert "Готово, присылайте документы" in env.replies()[0]


async def test_mention_is_case_insensitive(env):
    env.group(text=f"/start@MY_BOT {CODE}")
    await env.poller().poll_once()
    assert len(await env.chats()) == 1


@pytest.mark.parametrize("text", ["/start wrong", "/start", "/start КОД"])
async def test_start_wrong_or_no_code_refuses(env, text):
    env.push(text=text)
    await env.poller().poll_once()
    assert await env.chats() == []
    assert env.replies() == [REFUSED]


async def test_start_other_bot_ignored(env):
    env.group(text=f"/start@other_bot {CODE}")
    await env.poller().poll_once()
    assert await env.chats() == []
    assert env.replies() == []


async def test_startfoo_is_not_start(env):
    await env.link()
    env.push(text=f"/startfoo {CODE}")
    await env.poller().poll_once()
    assert env.replies() == [HINT]


async def test_linked_chat_start_without_code(env):
    await env.link()
    env.push(text="/start")
    await env.poller().poll_once()
    assert env.replies() == ["Этот чат уже подключён, присылайте документы"]


async def test_relink_updates_title_without_duplicate(env):
    await env.link(GROUP)
    env.group(text=f"/start {CODE}")
    await env.poller().poll_once()
    assert [(c.chat_id, c.title) for c in await env.chats()] == [(GROUP, "Бухгалтерия")]


async def test_rotated_code_old_link_dead_old_chats_stay(env):
    poller = env.poller()
    env.push(text=f"/start {CODE}")
    await poller.poll_once()
    await env.set_bot(invite_code="invite-code-2")

    other = {"id": 600, "type": "private", "first_name": "Пётр"}
    env.push(text=f"/start {CODE}", chat=other)
    await poller.poll_once()
    assert [c.chat_id for c in await env.chats()] == [CHAT]
    assert env.replies()[-1] == REFUSED

    # подключённый чат со старой ссылкой не отвязывается и не получает отказ
    env.push(text=f"/start {CODE}")
    await poller.poll_once()
    assert env.replies()[-1] == "Этот чат уже подключён, присылайте документы"

    env.push(text="/start invite-code-2", chat=other)
    await poller.poll_once()
    assert sorted(c.chat_id for c in await env.chats()) == [CHAT, 600]


# --- чужие чаты --------------------------------------------------------------


async def test_unlinked_chat_photo_ignored(env):
    env.photo("p1")
    env.push(text="привет")
    await env.poller().poll_once()
    assert env.methods("getFile") == []
    assert env.methods("sendMessage") == []
    assert await env.docs() == []
    assert (await env.bot()).update_offset == 102


async def test_unlinked_chat_album_ignored(env):
    poller = env.poller()
    env.album_part("p1", 11)
    await poller.poll_once()
    env.now += ALBUM_WAIT
    await poller.poll_once()
    assert env.methods("getFile") == []
    assert env.methods("sendMessage") == []
    assert await env.docs() == []


# --- приём -------------------------------------------------------------------


async def test_photo_largest_size_downloaded(env):
    await env.link()
    env.tg.files.update({"small": png_bytes((10, 10)), "big": png_bytes((30, 30)), "mid": png_bytes((20, 20))})
    env.push(
        photo=[
            {"file_id": "small", "width": 10, "height": 10},
            {"file_id": "big", "width": 30, "height": 30},
            {"file_id": "mid", "width": 20, "height": 20},
        ]
    )
    await env.poller().poll_once()
    assert [p["file_id"] for p in env.methods("getFile")] == ["big"]
    docs = await env.docs()
    assert len(docs) == 1 and docs[0].mode == "gray"


async def test_pdf_document_accepted(env):
    await env.link()
    env.pdf(message_id=42)
    await env.poller().poll_once()
    doc = (await env.docs())[0]
    assert doc.source == "telegram"
    assert doc.source_ref == {"bot_id": str(env.bot_id), "chat_id": CHAT, "message_id": 42, "media_group_id": None}
    assert doc.idempotency_key == f"tg:{env.bot_id}:{CHAT}:42"
    assert doc.pages == 2
    assert doc.tenant_id == env.tenant_id
    sent = env.methods("sendMessage")
    assert [p["text"] for p in sent] == ["Принял, распознаю (2 стр.)"]
    assert sent[0]["reply_parameters"]["message_id"] == 42


async def test_document_recognized_by_extension(env):
    await env.link()
    env.tg.files["f"] = pdf_bytes(1)
    env.push(document={"file_id": "f", "file_name": "SCAN.PDF", "mime_type": "application/octet-stream"})
    await env.poller().poll_once()
    assert len(await env.docs()) == 1


async def test_unsupported_file_reply(env):
    await env.link()
    env.tg.files["z"] = b"PK"
    env.push(document={"file_id": "z", "file_name": "a.zip", "mime_type": "application/zip"})
    await env.poller().poll_once()
    assert env.replies() == [HINT]
    assert env.methods("getFile") == []


async def test_private_text_reply(env):
    await env.link()
    env.push(text="привет")
    await env.poller().poll_once()
    assert env.replies() == [HINT]


async def test_group_text_silent(env):
    await env.link(GROUP)
    env.group(text="привет")
    env.group(sticker={"file_id": "s"})
    env.group(new_chat_members=[{"id": 1}])
    await env.poller().poll_once()
    assert env.replies() == []


@pytest.mark.parametrize("field", ["video", "audio", "voice"])
async def test_group_media_gets_hint(env, field):
    await env.link(GROUP)
    env.group(**{field: {"file_id": "v"}})
    await env.poller().poll_once()
    assert env.replies() == [HINT]
    assert env.methods("getFile") == []


async def test_group_unsupported_document_gets_hint(env):
    await env.link(GROUP)
    env.group(document={"file_id": "z", "file_name": "a.zip", "mime_type": "application/zip"})
    await env.poller().poll_once()
    assert env.replies() == [HINT]


async def test_oversize_refused_without_download(env):
    await env.link()
    size = 20 * 1024 * 1024 + 1
    env.push(document={"file_id": "big", "file_name": "a.pdf", "mime_type": "application/pdf", "file_size": size})
    await env.poller().poll_once()
    assert env.replies() == ["Файл больше 20 МБ: Telegram не отдаёт боту такие файлы, загрузите его в кабинете"]
    assert env.methods("getFile") == []
    assert await env.docs() == []


async def test_oversize_uses_lower_configured_limit(env):
    await env.link()
    env.settings.max_file_mb = 5
    size = 5 * 1024 * 1024 + 1
    env.push(document={"file_id": "big", "file_name": "a.pdf", "mime_type": "application/pdf", "file_size": size})
    await env.poller().poll_once()
    assert env.replies() == ["Файл больше 5 МБ: Telegram не отдаёт боту такие файлы, загрузите его в кабинете"]


async def test_bot_api_refuses_big_file_without_size(env):
    """В сообщении нет file_size, а getFile отвечает 400 file is too big: не повторяем и говорим про размер."""
    await env.link()
    env.push(document={"file_id": "f", "file_name": "a.pdf", "mime_type": "application/pdf"})
    env.tg.fail_with["getFile"] = (400, {"ok": False, "description": "Bad Request: file is too big"})
    await env.poller().poll_once()
    assert env.replies() == ["Файл больше 20 МБ: Telegram не отдаёт боту такие файлы, загрузите его в кабинете"]
    assert (await env.bot()).update_offset == 101


async def test_too_big_found_while_downloading(env):
    """Размера в сообщении нет, лимит срабатывает при скачивании."""
    await env.link()
    env.settings.max_file_mb = 1
    env.tg.files["f"] = b"x" * (1024 * 1024 + 1)
    env.push(document={"file_id": "f", "file_name": "a.pdf", "mime_type": "application/pdf"})
    await env.poller().poll_once()
    assert env.replies() == ["Файл больше 1 МБ: Telegram не отдаёт боту такие файлы, загрузите его в кабинете"]
    assert (await env.bot()).update_offset == 101


async def test_caption_selects_type(env):
    await env.link()
    env.photo("p1", caption="счет на оплату")
    env.photo("p2", caption="PASSPORT_RU")
    await env.poller().poll_once()
    assert sorted(d.schema_slug for d in await env.docs()) == ["invoice_ru", "passport_ru"]


async def test_default_schema_and_auto(env):
    await env.link()
    await env.set_bot(default_schema="invoice_ru")
    poller = env.poller()
    env.photo("p1", caption="непонятная подпись")
    await poller.poll_once()
    await env.set_bot(default_schema=None)
    env.photo("p2")
    await poller.poll_once()
    assert sorted((d.schema_slug, d.schema_version) for d in await env.docs()) == [("auto", 0), ("invoice_ru", 1)]


async def test_limit_reply_exact(env):
    await env.link()
    async with env.sm() as s:
        tenant = await s.get(Tenant, env.tenant_id)
        tenant.pages_limit = 50
        used = {"schema_slug": "invoice_ru", "schema_version": 1, "files": [], "mode": "gray", "pages": 50}
        s.add(Document(tenant_id=env.tenant_id, **used))
        await s.commit()
    env.photo("p1")
    await env.poller().poll_once()
    assert env.replies() == ["Лимит страниц на месяц исчерпан (50 из 50)"]
    assert env.methods("getFile") == []
    assert len(await env.docs()) == 1


# --- альбомы -----------------------------------------------------------------


async def test_album_one_document_in_order(env):
    await env.link()
    env.album_part("c", 13)
    env.album_part("a", 11)
    env.album_part("b", 12, caption="passport_ru")
    poller = env.poller()
    await poller.poll_once()
    assert await env.docs() == []
    assert (await env.bot()).update_offset == 100  # пока альбом ждёт, offset не уходит за его части

    env.now += ALBUM_WAIT
    await poller.poll_once()
    assert [p["timeout"] for p in env.methods("getUpdates")] == [25, 0]
    docs = await env.docs()
    assert len(docs) == 1
    doc = docs[0]
    assert doc.schema_slug == "passport_ru"
    assert doc.pages == 3
    assert doc.idempotency_key == f"tg:{env.bot_id}:{CHAT}:album:g1"
    assert doc.source_ref == {"bot_id": str(env.bot_id), "chat_id": CHAT, "message_id": 11, "media_group_id": "g1"}
    contents = [await env.deps.intake.blobs.get(key) for key in doc.files]
    assert contents == [env.tg.files["a"], env.tg.files["b"], env.tg.files["c"]]
    assert env.replies() == ["Принял, распознаю (3 стр.)"]
    assert env.methods("sendMessage")[0]["reply_parameters"]["message_id"] == 11
    assert (await env.bot()).update_offset == 103


async def test_album_split_across_polls(env):
    await env.link()
    poller = env.poller()
    env.album_part("a", 11)
    await poller.poll_once()
    env.now += ALBUM_WAIT - 0.5
    env.album_part("b", 12)
    await poller.poll_once()
    env.now += ALBUM_WAIT - 0.5  # с первой части прошло больше, с последней — ещё нет
    await poller.poll_once()
    assert await env.docs() == []
    env.now += 0.5
    await poller.poll_once()
    docs = await env.docs()
    assert len(docs) == 1 and docs[0].pages == 2


async def test_restart_mid_album_no_loss(env):
    await env.link()
    env.album_part("a", 11)
    env.album_part("b", 12)
    await env.poller().poll_once()

    second = env.poller()
    env.album_part("c", 13)
    await second.poll_once()
    assert await env.docs() == []
    env.now += ALBUM_WAIT
    await second.poll_once()
    docs = await env.docs()
    assert len(docs) == 1 and docs[0].pages == 3
    assert (await env.bot()).update_offset == 103


async def test_message_after_waiting_album_not_repeated(env):
    """Сообщение за альбомом обработано; пока offset держит альбом, повторную выдачу отсекает _seen."""
    await env.link()
    poller = env.poller()
    env.album_part("a", 11)
    env.push(text="привет", message_id=20)
    env.photo("solo", message_id=21)
    await poller.poll_once()
    assert (await env.bot()).update_offset == 100
    await poller.poll_once()
    assert env.replies() == [HINT, ACCEPTED_ONE]
    env.now += ALBUM_WAIT
    await poller.poll_once()
    assert len(await env.docs()) == 2
    assert (await env.bot()).update_offset == 103


async def test_album_download_failure_retried_without_loss(env):
    await env.link()
    poller = env.poller()
    env.album_part("a", 11)
    env.album_part("b", 12)
    await poller.poll_once()
    env.now += ALBUM_WAIT
    env.tg.fail_with["getFile"] = 500
    await poller.poll_once()
    assert await env.docs() == []
    assert (await env.bot()).update_offset == 100
    del env.tg.fail_with["getFile"]
    await poller.poll_once()
    docs = await env.docs()
    assert len(docs) == 1 and docs[0].pages == 2
    assert (await env.bot()).update_offset == 102


async def test_album_download_gives_up(env):
    await env.link()
    poller = env.poller()
    env.album_part("a", 11)
    await poller.poll_once()
    env.now += ALBUM_WAIT
    env.tg.fail_with["getFile"] = 500
    for _ in range(DOWNLOAD_RETRIES):
        await poller.poll_once()
    assert env.replies() == [DOWNLOAD_FAILED]
    assert (await env.bot()).update_offset == 101


async def album_docs(env) -> list[Document]:
    return [d for d in await env.docs() if d.source_ref["media_group_id"]]


async def test_slow_batch_does_not_truncate_album(env):
    """Пока качается чужой файл, Telegram получил ещё часть альбома: сборке нужен свежий опрос."""
    await env.link()
    poller = env.poller()
    env.album_part("a", 11)
    env.photo("slow", message_id=20)
    state = {"done": False}

    def on_call(method):
        if method == "getFile" and not state["done"]:
            state["done"] = True
            env.now += ALBUM_WAIT + 1
            env.album_part("b", 12)

    env.tg.on_call = on_call
    await poller.poll_once()
    assert await album_docs(env) == []
    await poller.poll_once()
    assert await album_docs(env) == []
    env.now += ALBUM_WAIT
    await poller.poll_once()
    assert [d.pages for d in await album_docs(env)] == [2]


async def test_failed_download_does_not_truncate_album(env):
    """Проход остановился на упавшем файле раньше части B: альбом не собирается без неё."""
    await env.link()
    poller = env.poller()
    env.album_part("a", 11)
    env.photo("flaky", message_id=20)
    env.album_part("b", 12)
    env.tg.fail_files.add("flaky")
    await poller.poll_once()
    env.now += ALBUM_WAIT + 3
    await poller.poll_once()
    assert await album_docs(env) == []
    env.tg.fail_files.clear()
    await poller.poll_once()
    assert await album_docs(env) == []
    env.now += ALBUM_WAIT
    await poller.poll_once()
    assert [d.pages for d in await album_docs(env)] == [2]
    assert (await env.bot()).update_offset == 103


LATE_PART = "Эта часть альбома пришла позже остальных и не вошла в документ. Пришлите её отдельным сообщением."


async def test_late_album_part_gets_reply_once(env):
    await env.link()
    poller = env.poller()
    env.album_part("a", 11)
    env.album_part("b", 12)
    await poller.poll_once()
    env.now += ALBUM_WAIT
    await poller.poll_once()

    env.album_part("c", 13)
    await poller.poll_once()
    env.now += ALBUM_WAIT
    await poller.poll_once()
    await poller.poll_once()
    assert env.replies() == ["Принял, распознаю (2 стр.)", LATE_PART]
    assert env.methods("sendMessage")[1]["reply_parameters"]["message_id"] == 13
    docs = await env.docs()
    assert len(docs) == 1 and docs[0].pages == 2
    assert (await env.bot()).update_offset == 103


async def test_late_album_part_recognized_after_restart(env):
    await env.link()
    first = env.poller()
    env.album_part("a", 11)
    await first.poll_once()
    env.now += ALBUM_WAIT
    await first.poll_once()

    second = env.poller()
    env.album_part("b", 12)
    await second.poll_once()
    env.now += ALBUM_WAIT
    await second.poll_once()
    assert env.replies() == [ACCEPTED_ONE, LATE_PART]


async def test_whole_album_redelivery_after_restart_silent(env):
    await env.link()
    first = env.poller()
    env.album_part("a", 11)
    env.album_part("b", 12)
    await first.poll_once()
    env.now += ALBUM_WAIT
    await first.poll_once()

    await env.set_bot(update_offset=0)  # упали после создания документа, но до сохранения offset
    second = env.poller()
    await second.poll_once()
    env.now += ALBUM_WAIT
    await second.poll_once()
    assert env.replies() == ["Принял, распознаю (2 стр.)"]
    assert len(await env.docs()) == 1


async def test_redelivered_album_with_extra_part_logged(env, caplog):
    """Из повторной выдачи опоздавшую часть не вычленить: без ответа, но с предупреждением в логе."""
    await env.link()
    first = env.poller()
    env.album_part("a", 11)
    await first.poll_once()
    env.now += ALBUM_WAIT
    await first.poll_once()

    await env.set_bot(update_offset=0)
    env.album_part("b", 12)
    second = env.poller()
    await second.poll_once()
    env.now += ALBUM_WAIT
    await second.poll_once()
    assert env.replies() == [ACCEPTED_ONE]
    assert "лишние части" in caplog.text


# --- смена нумерации после долгого молчания ------------------------------------


def _offsets(env) -> list:
    return [payload.get("offset") for payload in env.methods("getUpdates")]


async def test_silent_bot_forgets_offset_before_telegram_renumbers(env):
    await env.link()
    await env.set_bot(update_offset=900, last_update_at=datetime.now(UTC) - timedelta(days=6, minutes=1))
    poller = env.poller()
    await poller.poll_once()
    assert _offsets(env) == [None]

    # Telegram начал нумерацию заново: без offset новое сообщение доходит, хотя номер ниже сохранённого
    env.pdf()
    await poller.poll_once()
    assert _offsets(env) == [None, None]
    assert len(await env.docs()) == 1
    bot = await env.bot()
    assert bot.update_offset == 101
    assert datetime.now(UTC) - bot.last_update_at < timedelta(minutes=1)

    await poller.poll_once()
    assert _offsets(env)[-1] == 101


async def test_recent_update_keeps_offset(env):
    await env.set_bot(update_offset=900, last_update_at=datetime.now(UTC) - timedelta(days=5))
    await env.poller().poll_once()
    assert _offsets(env) == [900]


async def test_bot_without_updates_counts_from_creation(env):
    await env.set_bot(update_offset=900, created_at=datetime.now(UTC) - timedelta(days=7))
    await env.poller().poll_once()
    assert _offsets(env) == [None]


async def test_unrelated_writes_do_not_keep_offset_alive(env):
    """updated_at двигают ошибки отправки и правки в кабинете: молчащий бот всё равно забывает offset."""
    await env.set_bot(
        update_offset=900, last_update_at=datetime.now(UTC) - timedelta(days=7), last_error="Отправка в Telegram: x"
    )
    assert datetime.now(UTC) - (await env.bot()).updated_at < timedelta(minutes=1)
    await env.poller().poll_once()
    assert _offsets(env) == [None]


async def test_update_below_offset_resets_it(env):
    await env.link()
    await env.set_bot(update_offset=900)
    env.pdf()  # update_id 100
    env.tg.below_offset = list(env.tg.updates)
    poller = env.poller()
    await poller.poll_once()
    assert len(await env.docs()) == 1
    assert (await env.bot()).update_offset == 101  # ниже прежнего: условие «только растёт» здесь не действует

    env.pdf("pdf2")
    await poller.poll_once()
    assert _offsets(env) == [900, 101]
    assert len(await env.docs()) == 2
    assert (await env.bot()).update_offset == 102


async def test_failed_download_after_renumbering_is_retried(env):
    await env.link()
    await env.set_bot(update_offset=900)
    env.pdf()
    env.tg.below_offset = list(env.tg.updates)
    env.tg.fail_with["getFile"] = 500
    poller = env.poller()
    await poller.poll_once()
    assert (await env.bot()).update_offset == 100  # не дальше несделанного

    del env.tg.fail_with["getFile"]
    await poller.poll_once()
    assert len(await env.docs()) == 1
    assert (await env.bot()).update_offset == 101


async def test_album_buffered_when_numbering_changes_keeps_nothing_stuck(env):
    await env.link()
    env.album_part("a1", 1)  # update_id 100
    poller = env.poller()
    await poller.poll_once()
    assert (await env.bot()).update_offset == 100

    env.pdf("pdf2", message_id=50)
    new_number = {**env.tg.updates.pop(), "update_id": 40}
    env.tg.below_offset = [new_number]
    env.now += ALBUM_WAIT
    env.tg.updates.clear()
    await poller.poll_once()
    assert (await env.bot()).update_offset == 41
    assert len(await env.docs()) == 2


# --- идемпотентность ----------------------------------------------------------


async def test_redelivery_no_second_document(env):
    await env.link()
    env.pdf()
    await env.poller().poll_once()
    async with env.sm() as s:
        exports_before = await s.scalar(select(func.count()).select_from(Export))

    await env.set_bot(update_offset=0)
    await env.poller().poll_once()
    assert len(await env.docs()) == 1
    assert env.replies() == ["Принял, распознаю (2 стр.)"]
    assert len(env.methods("getFile")) == 1  # повтор отсекается раньше скачивания
    async with env.sm() as s:
        assert await s.scalar(select(func.count()).select_from(Export)) == exports_before


async def test_offset_saved_after_document(env):
    await env.link()
    env.pdf()
    env.tg.fail_with["getFile"] = 500
    poller = env.poller()
    await poller.poll_once()
    assert await env.docs() == []
    assert (await env.bot()).update_offset == 0
    assert env.replies() == []

    del env.tg.fail_with["getFile"]
    await poller.poll_once()
    assert len(await env.docs()) == 1
    assert (await env.bot()).update_offset == 101


async def test_failed_download_does_not_repeat_earlier_messages(env):
    await env.link()
    env.photo("ok", message_id=1)
    env.push(text="текст", message_id=2)
    env.photo("flaky", message_id=3)
    env.photo("later", message_id=4)
    env.tg.fail_files.add("flaky")
    poller = env.poller()

    await poller.poll_once()
    assert [d.source_ref["message_id"] for d in await env.docs()] == [1]
    assert (await env.bot()).update_offset == 102  # стоим на третьем, первые два пройдены
    assert env.replies() == [ACCEPTED_ONE, HINT]
    assert [p["file_id"] for p in env.methods("getFile")] == ["ok", "flaky"]  # за упавшее сообщение не забегаем

    env.tg.fail_files.clear()
    await poller.poll_once()
    assert sorted(d.source_ref["message_id"] for d in await env.docs()) == [1, 3, 4]
    assert env.replies() == [ACCEPTED_ONE, HINT, ACCEPTED_ONE, ACCEPTED_ONE]
    assert (await env.bot()).update_offset == 104


async def test_offset_survives_restart(env):
    await env.link()
    env.photo("p1")
    await env.poller().poll_once()
    await env.poller().poll_once()
    assert [p["offset"] for p in env.methods("getUpdates")] == [0, 101]
    assert len(await env.docs()) == 1


async def test_offset_never_moves_back(env):
    """Сохранение условное: устаревший поллер не откатывает offset, записанный другим."""
    poller = env.poller()
    env.push(text="/start")
    await poller.poll_once()
    await env.set_bot(update_offset=500)
    env.push(text="/start")
    await poller.poll_once()
    assert (await env.bot()).update_offset == 500


# --- сбои --------------------------------------------------------------------


async def test_revoked_token_stops_polling(env):
    env.tg.fail_with["getUpdates"] = 401
    await env.poller().run()  # без отзыва не завершился бы
    bot = await env.bot()
    assert bot.active is False
    assert bot.last_error == "токен больше не действует"


async def test_revoked_while_downloading_stops_and_keeps_update(env):
    await env.link()
    env.photo("p1")
    env.tg.fail_with["getFile"] = 401
    await env.poller().run()
    bot = await env.bot()
    assert bot.active is False
    assert bot.update_offset == 0
    assert env.replies() == []


async def test_group_migration_keeps_link(env):
    await env.link(GROUP)
    env.group(migrate_to_chat_id=-100700)
    await env.poller().poll_once()
    assert [c.chat_id for c in await env.chats()] == [-100700]
    assert env.replies() == []


async def test_group_migration_when_new_chat_already_linked(env):
    await env.link(GROUP)
    await env.link(-100700)
    env.group(migrate_to_chat_id=-100700)
    await env.poller().poll_once()
    assert [c.chat_id for c in await env.chats()] == [-100700]


async def test_download_retries_then_gives_up(env):
    await env.link()
    env.photo("p1")
    env.photo("p2")
    env.tg.fail_with["getFile"] = 500
    poller = env.poller()
    for _ in range(DOWNLOAD_RETRIES - 1):
        await poller.poll_once()
        assert (await env.bot()).update_offset == 0
        assert env.replies() == []
    await poller.poll_once()
    assert env.replies() == [DOWNLOAD_FAILED]
    assert (await env.bot()).update_offset == 101  # следующее сообщение ждёт своих попыток
    del env.tg.fail_with["getFile"]
    await poller.poll_once()
    assert [d.source_ref["message_id"] for d in await env.docs()] == [2]
    assert (await env.bot()).update_offset == 102


async def test_missing_file_not_retried(env):
    """404 файла при живом токене: повторять незачем, бот не отключается."""
    await env.link()
    env.push(document={"file_id": "gone", "file_name": "a.pdf", "mime_type": "application/pdf"})
    await env.poller().poll_once()
    assert env.replies() == [DOWNLOAD_FAILED]
    bot = await env.bot()
    assert bot.active is True
    assert bot.update_offset == 101


async def test_unexpected_error_does_not_jam(env, monkeypatch):
    await env.link()
    real = bot_poller.accept_document
    calls = []

    async def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("сбой")
        return await real(*args, **kwargs)

    monkeypatch.setattr(bot_poller, "accept_document", flaky)
    env.photo("p1")
    env.photo("p2")
    await env.poller().poll_once()
    assert env.replies() == ["Не удалось принять документ. Пришлите его ещё раз", ACCEPTED_ONE]
    assert [d.source_ref["message_id"] for d in await env.docs()] == [2]
    assert (await env.bot()).update_offset == 102


async def test_reply_failure_keeps_document(env):
    await env.link()
    env.pdf()
    env.tg.fail_with["sendMessage"] = 500
    await env.poller().poll_once()
    assert len(await env.docs()) == 1
    assert (await env.bot()).update_offset == 101


async def test_intake_error_replied(env):
    await env.link()
    env.tg.files["f"] = b"not an image"
    env.push(document={"file_id": "f", "file_name": "a.pdf", "mime_type": "application/pdf"})
    await env.poller().poll_once()
    assert "неподдерживаемый формат" in env.replies()[0]
    assert await env.docs() == []
    assert (await env.bot()).update_offset == 101


async def test_failures_recorded_with_growing_pause(env):
    poller = env.poller()
    pauses = []

    async def sleep(seconds):
        pauses.append(seconds)
        if len(pauses) == 6:
            env.tg.fail_with["getUpdates"] = 401

    poller._sleep = sleep
    env.tg.fail_with["getUpdates"] = 500
    await poller.run()
    assert pauses == [5, 10, 20, 40, 60, 60]


async def test_conflict_recorded_and_paused(env):
    poller = env.poller()
    pauses = []
    errors = []

    async def sleep(seconds):
        pauses.append(seconds)
        errors.append((await env.bot()).last_error)
        env.tg.fail_with["getUpdates"] = 401

    poller._sleep = sleep
    env.tg.fail_with["getUpdates"] = 409
    await poller.run()
    assert pauses == [CONFLICT_PAUSE]
    assert errors[0].startswith("Опрос Telegram: Telegram getUpdates: 409")


async def test_last_error_cleared_after_recovery(env):
    poller = env.poller()
    env.tg.fail_with["getUpdates"] = 500
    await poller.poll_once()
    assert (await env.bot()).last_error is not None
    del env.tg.fail_with["getUpdates"]
    await poller.poll_once()
    assert (await env.bot()).last_error is None


async def test_stored_own_error_cleared_by_new_poller(env):
    """Ошибка прошлого процесса: новый экземпляр о ней не помнит, но узнаёт свою по началу текста."""
    env.tg.fail_with["getUpdates"] = 500
    await env.poller().poll_once()
    assert (await env.bot()).last_error.startswith("Опрос Telegram: ")
    del env.tg.fail_with["getUpdates"]
    await env.poller().poll_once()
    assert (await env.bot()).last_error is None


async def test_foreign_error_survives_new_poller(env):
    await env.set_bot(last_error="не отправлено в чат")
    await env.poller().poll_once()
    assert (await env.bot()).last_error == "не отправлено в чат"


async def test_foreign_last_error_survives_poll(env):
    """last_error пишет и отправка ответов: успешный опрос чужую ошибку не стирает."""
    await env.set_bot(last_error="не отправлено в чат")
    poller = env.poller()
    await poller.poll_once()
    await poller.poll_once()
    assert (await env.bot()).last_error == "не отправлено в чат"


async def test_foreign_error_written_after_own_survives_recovery(env):
    poller = env.poller()
    env.tg.fail_with["getUpdates"] = 500
    await poller.poll_once()
    await env.set_bot(last_error="не отправлено в чат")
    del env.tg.fail_with["getUpdates"]
    await poller.poll_once()
    assert (await env.bot()).last_error == "не отправлено в чат"


async def test_db_failure_on_link_check_keeps_update(env, monkeypatch):
    """Сбой БД при проверке привязки — сбой опроса: сообщение не теряется и в чужой чат ничего не уходит."""
    await env.link()
    env.photo("p1")
    poller = env.poller()
    real = poller._is_linked

    async def boom(chat_id):
        raise OSError("база недоступна")

    monkeypatch.setattr(poller, "_is_linked", boom)
    await poller.poll_once()
    assert env.replies() == []
    assert (await env.bot()).update_offset == 0
    assert (await env.bot()).last_error == "Опрос Telegram: сбой (OSError)"

    monkeypatch.setattr(poller, "_is_linked", real)
    await poller.poll_once()
    assert len(await env.docs()) == 1
    assert (await env.bot()).update_offset == 101


async def test_run_paces_album_wait_and_download_retries(env):
    """timeout=0 и повтор скачивания отвечают сразу: без паузы run крутился бы вхолостую."""
    await env.link()
    poller = env.poller()
    pauses = []

    async def sleep(seconds):
        pauses.append(seconds)
        env.now += 1.0

    def on_call(method):
        if method == "getFile" and len(pauses) >= 3:
            env.tg.fail_with.pop("getFile", None)
        if len(env.methods("getUpdates")) == 5:
            env.tg.fail_with["getUpdates"] = 401

    poller._sleep = sleep
    env.tg.on_call = on_call
    env.tg.fail_with["getFile"] = 500
    env.album_part("a", 11)
    await poller.run()
    assert pauses == [ALBUM_TICK, ALBUM_TICK, 5]
    assert len(await env.docs()) == 1


async def test_stops_when_bot_deactivated(env):
    await env.link()
    env.photo("p1")
    await env.set_bot(active=False)
    await env.poller().run()
    assert await env.docs() == []
    assert env.replies() == []


async def test_stops_when_deactivated_during_long_poll(env):
    await env.link()
    poller = env.poller()
    await poller.poll_once()
    env.photo("p1")
    real = poller._api.get_updates

    async def get_updates(offset, timeout):
        await env.set_bot(active=False)  # отключили в кабинете, пока висел запрос
        return await real(offset, timeout)

    poller._api.get_updates = get_updates
    await poller.run()
    assert await env.docs() == []
    assert env.replies() == []


# --- транзакции и секреты ------------------------------------------------------


async def test_no_connection_held_during_telegram_calls(env):
    """На любом обращении к Telegram соединение с БД свободно: транзакция не висит на сетевом вызове."""
    engine = env.sm.kw["bind"]
    held = {"n": 0}

    def checkout(*_args):
        held["n"] += 1

    def checkin(*_args):
        held["n"] -= 1

    event.listen(engine.sync_engine, "checkout", checkout)
    event.listen(engine.sync_engine, "checkin", checkin)
    calls = []
    env.tg.on_call = lambda method: calls.append((method, held["n"]))

    await env.link()
    poller = env.poller()
    env.push(text=f"/start {CODE}", chat={"id": 900, "type": "private", "first_name": "Новый"})
    env.pdf()
    env.album_part("a", 11)
    env.album_part("b", 12)
    env.push(text="привет")
    await poller.poll_once()
    env.now += ALBUM_WAIT
    await poller.poll_once()

    assert {m for m, _ in calls} >= {"getUpdates", "getFile", "download", "sendMessage"}
    assert [c for c in calls if c[1] != 0] == []
    assert len(await env.docs()) == 2


async def test_token_never_logged_or_stored(env, caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    await env.link()

    def url_error(method):
        return httpx.ConnectError(f"https://api.telegram.org/bot{TOKEN}/{method}")

    env.tg.fail_with["getUpdates"] = url_error("getUpdates")
    poller = env.poller()
    await poller.poll_once()
    assert TOKEN not in (await env.bot()).last_error
    del env.tg.fail_with["getUpdates"]

    env.photo("p1")
    env.tg.fail_with["getFile"] = url_error("getFile")
    for _ in range(DOWNLOAD_RETRIES):
        await poller.poll_once()
    del env.tg.fail_with["getFile"]

    async def boom(*args, **kwargs):
        raise RuntimeError(f"секрет {TOKEN}")

    monkeypatch.setattr(bot_poller, "accept_document", boom)
    env.photo("p2")
    env.tg.fail_with["sendMessage"] = url_error("sendMessage")
    await poller.poll_once()

    assert "RuntimeError" in caplog.text
    assert TOKEN not in caplog.text
    assert "секрет" not in caplog.text
    assert (await env.bot()).update_offset == 102
