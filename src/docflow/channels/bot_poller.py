"""Опрос Telegram-бота компании: привязка чатов по пригласительной ссылке и приём документов.

Ни одна транзакция не живёт дольше обращения к БД: long-poll, скачивание и ответы идут без открытого соединения.
"""

import asyncio
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from uuid import UUID

import httpx
from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from docflow.channels.botapi import (
    MAX_DOWNLOAD_BYTES,
    BotApi,
    BotApiError,
    BotConflict,
    BotFileTooBig,
    BotTokenRevoked,
)
from docflow.channels.deps import ChannelDeps
from docflow.db.models import BotChat, Document, Tenant, TenantBot
from docflow.intake import IntakeError, IntakeFile, accept_document
from docflow.schemas.catalog import AUTO_SLUG, list_doc_types, match_doc_type
from docflow.secrets import decrypt_secrets

logger = logging.getLogger(__name__)

ALBUM_WAIT = 2.0
DOWNLOAD_RETRIES = 5
SCAN_MODE = "gray"
# пока альбом дозревает, getUpdates с timeout=0 отвечает сразу: без паузы цикл долбил бы Telegram
ALBUM_TICK = 0.5
CONFLICT_PAUSE = 30.0
PAUSE_MIN = 5.0
PAUSE_MAX = 60.0

START_RE = re.compile(r"^/start(@(\w+))?(?:\s+(\S+))?")
MB = 1024 * 1024
FILE_LIKE_FIELDS = ("document", "video", "audio", "voice")
ALLOWED_MIME = {
    "image/jpeg",
    "image/png",
    "image/webp",
    "image/tiff",
    "image/heic",
    "image/heif",
    "application/pdf",
}
ALLOWED_EXTENSIONS = (".jpg", ".jpeg", ".png", ".webp", ".tif", ".tiff", ".heic", ".heif", ".pdf")

TOKEN_REVOKED = "токен больше не действует"
# last_error общий с отправкой ответов (задача 9): по этому началу опрос узнаёт свои ошибки и после перезапуска
POLL_ERROR_PREFIX = "Опрос Telegram: "
ASK_FOR_LINK = "Попросите ссылку у администратора кабинета"
ALREADY_LINKED = "Этот чат уже подключён, присылайте документы"
NEED_FILE = "Пришлите фото или PDF документа"
DOWNLOAD_FAILED = "Не удалось скачать файл. Пришлите его ещё раз"
ACCEPT_FAILED = "Не удалось принять документ. Пришлите его ещё раз"
LATE_ALBUM_PART = "Эта часть альбома пришла позже остальных и не вошла в документ. Пришлите её отдельным сообщением."


@dataclass(frozen=True)
class _BotInfo:
    """Свежий снимок настроек: код приглашения и тип по умолчанию меняются в кабинете на ходу."""

    bot: TenantBot
    tenant_name: str


@dataclass(frozen=True)
class _File:
    file_id: str
    name: str
    size: int | None


@dataclass
class _Album:
    media_group_id: str
    last_at: float
    parts: dict[int, dict] = field(default_factory=dict)  # message_id → сообщение
    update_ids: set[int] = field(default_factory=set)


def _start_match(text: str) -> re.Match | None:
    m = START_RE.match(text)
    # выражение не закреплено справа: без этой проверки /startfoo считался бы командой /start
    if m is None or (m.end() < len(text) and not text[m.end()].isspace()):
        return None
    return m


def _chat_title(chat: dict) -> str:
    name = " ".join(p for p in (chat.get("first_name"), chat.get("last_name")) if p)
    return chat.get("title") or name or chat.get("username") or str(chat["id"])


def _pick_file(message: dict) -> _File | None:
    photo = message.get("photo")
    if photo:
        largest = max(photo, key=lambda p: p.get("width", 0) * p.get("height", 0))
        return _File(largest["file_id"], f"photo_{message['message_id']}.jpg", largest.get("file_size"))
    document = message.get("document")
    if document:
        name = document.get("file_name") or ""
        if document.get("mime_type") in ALLOWED_MIME or name.lower().endswith(ALLOWED_EXTENSIONS):
            return _File(document["file_id"], name or f"document_{message['message_id']}", document.get("file_size"))
    return None


def _wants_hint(messages: list[dict]) -> bool:
    """В группе бот молчит на обычную переписку и отвечает только на попытку прислать файл."""
    if messages[0]["chat"].get("type") == "private":
        return True
    return any(name in m for m in messages for name in FILE_LIKE_FIELDS)


def _is_addressable(message: dict) -> bool:
    chat = message.get("chat")
    return isinstance(chat, dict) and isinstance(chat.get("id"), int) and isinstance(message.get("message_id"), int)


class BotPoller:
    def __init__(self, deps: ChannelDeps, bot_id: UUID, http: httpx.AsyncClient, clock=time.monotonic):
        self._deps = deps
        self._bot_id = bot_id
        self._http = http
        self._clock = clock
        self._sleep = asyncio.sleep
        self._api: BotApi | None = None
        self._offset = 0  # что сохранено в БД и передаётся в getUpdates
        self._next_offset = 0  # за последним пройденным обновлением
        self._seen: set[int] = set()
        self._albums: dict[tuple[int, str], _Album] = {}
        self._attempts: dict[str, int] = {}  # ключ идемпотентности → неудачные скачивания
        self._retrying = False
        self._stopped = False
        self._pause = 0.0
        self._backoff = PAUSE_MIN

    async def run(self) -> None:
        while not self._stopped:
            await self.poll_once()
            if self._pause:
                await self._sleep(self._pause)

    async def poll_once(self, timeout: int = 25) -> None:
        """Один getUpdates и его обработка. Сбои опроса не выбрасывает: пишет last_error и назначает паузу для run."""
        self._pause = 0.0
        try:
            await self._poll(timeout)
        except BotTokenRevoked:
            logger.warning("бот %s: токен отозван", self._bot_id)
            if await self._save_status(active=False, last_error=TOKEN_REVOKED):
                self._stopped = True
            else:
                self._pause = self._grow_backoff()
        except BotConflict as exc:
            logger.warning("бот %s: параллельный опрос или вебхук", self._bot_id)
            await self._record_error(exc.message)
            self._pause = CONFLICT_PAUSE
        except Exception as exc:
            logger.warning("бот %s: сбой опроса: %s", self._bot_id, type(exc).__name__)
            # в тексте чужого исключения мог оказаться адрес с токеном; BotApiError уже очищен
            text = exc.message if isinstance(exc, BotApiError) else f"сбой ({type(exc).__name__})"
            await self._record_error(text)
            self._pause = self._grow_backoff()

    async def _poll(self, timeout: int) -> None:
        if self._api is None:
            info = await self._load_bot()
            if info is None:
                self._stopped = True
                return
            self._offset = self._next_offset = info.bot.update_offset
            token = decrypt_secrets(info.bot.token_enc, self._deps.settings.secrets_key)["token"]
            self._api = BotApi(self._http, token, self._deps.settings.telegram_api_url)

        updates = await self._api.get_updates(self._offset, 0 if self._albums else timeout)
        # альбом готов, только если свежий опрос не принёс новых частей: время обработки пачки не считается
        polled_at = self._clock()

        # за время опроса бота могли отключить, а код приглашения — выпустить новый
        info = await self._load_bot()
        if info is None:
            self._stopped = True
            return
        if (info.bot.last_error or "").startswith(POLL_ERROR_PREFIX):
            own = TenantBot.last_error.startswith(POLL_ERROR_PREFIX, autoescape=True)
            await self._save_status(own, last_error=None)

        self._retrying = False
        stopped_early = False
        for item in sorted(updates, key=lambda u: u["update_id"]):
            update_id = item["update_id"]
            if update_id in self._seen:
                continue
            message = item.get("message")
            # упавшее скачивание останавливает проход: это и всё следующее придёт снова, раньше прошедшее отсечёт _seen
            if isinstance(message, dict) and not await self._take(info, message, update_id):
                stopped_early = True
                break
            self._seen.add(update_id)
            self._next_offset = max(self._next_offset, update_id + 1)
        # за остановившимся проходом могут лежать ещё не прочитанные части альбомов
        if not stopped_early:
            await self._flush_albums(info, polled_at)
        await self._save_offset()

        if self._retrying:
            self._pause = self._grow_backoff()
            return
        self._backoff = PAUSE_MIN
        if self._albums:
            self._pause = ALBUM_TICK

    def _grow_backoff(self) -> float:
        pause = self._backoff
        self._backoff = min(pause * 2, PAUSE_MAX)
        return pause

    async def _load_bot(self) -> _BotInfo | None:
        """None — бота отключили: опрашивать больше незачем."""
        async with self._deps.sessionmaker() as session:
            row = (
                await session.execute(
                    select(TenantBot, Tenant.name)
                    .join(Tenant, Tenant.id == TenantBot.tenant_id)
                    .where(TenantBot.id == self._bot_id, TenantBot.active.is_(True))
                )
            ).first()
        return _BotInfo(row[0], row[1]) if row is not None else None

    async def _record_error(self, text: str) -> None:
        await self._save_status(last_error=f"{POLL_ERROR_PREFIX}{text}")

    async def _save_status(self, *where, **values) -> bool:
        try:
            async with self._deps.sessionmaker() as session:
                await session.execute(
                    update(TenantBot)
                    .where(TenantBot.id == self._bot_id, *where)
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
        except Exception as exc:
            logger.warning("бот %s: не удалось сохранить состояние: %s", self._bot_id, type(exc).__name__)
            return False
        return True

    async def _save_offset(self) -> None:
        pending = [min(album.update_ids) for album in self._albums.values()]
        offset = min([self._next_offset, *pending])
        if offset > self._offset:
            async with self._deps.sessionmaker() as session:
                await session.execute(
                    update(TenantBot)
                    .where(TenantBot.id == self._bot_id, TenantBot.update_offset < offset)
                    .values(update_offset=offset)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
            self._offset = offset
        self._seen = {u for u in self._seen if u >= self._offset}

    async def _take(self, info: _BotInfo, message: dict, update_id: int) -> bool:
        """False — обновление нужно получить снова, дальше него идти нельзя."""
        if not _is_addressable(message):
            return True
        media_group_id = message.get("media_group_id")
        if media_group_id is not None:
            self._buffer_album_part(message, str(media_group_id), update_id)
            return True
        chat_id = message["chat"]["id"]
        new_chat_id = message.get("migrate_to_chat_id")
        if isinstance(new_chat_id, int):
            await self._migrate_chat(chat_id, new_chat_id)
            return True
        text = message.get("text")
        start = _start_match(text) if isinstance(text, str) else None
        if start is not None:
            await self._handle_start(info, message["chat"], start)
            return True
        return await self._accept(info, [message], None)

    def _buffer_album_part(self, message: dict, media_group_id: str, update_id: int) -> None:
        key = (message["chat"]["id"], media_group_id)
        album = self._albums.setdefault(key, _Album(media_group_id, self._clock()))
        album.parts[message["message_id"]] = message
        album.update_ids.add(update_id)
        album.last_at = self._clock()

    async def _flush_albums(self, info: _BotInfo, polled_at: float) -> None:
        due = [key for key, album in self._albums.items() if polled_at - album.last_at >= ALBUM_WAIT]
        for key in due:
            album = self._albums[key]
            messages = [album.parts[message_id] for message_id in sorted(album.parts)]
            # не вышло — альбом остаётся в буфере и держит offset, следующий проход повторит
            if await self._accept(info, messages, album.media_group_id):
                del self._albums[key]

    async def _migrate_chat(self, old_chat_id: int, new_chat_id: int) -> None:
        async with self._deps.sessionmaker() as session:
            taken = await session.scalar(
                select(BotChat.id).where(BotChat.bot_id == self._bot_id, BotChat.chat_id == new_chat_id)
            )
            old = BotChat.bot_id == self._bot_id, BotChat.chat_id == old_chat_id
            if taken is not None:
                # новую супергруппу успели подключить /start: две строки на один чат не нужны
                await session.execute(delete(BotChat).where(*old).execution_options(synchronize_session=False))
            else:
                await session.execute(
                    update(BotChat).where(*old).values(chat_id=new_chat_id).execution_options(synchronize_session=False)
                )
            await session.commit()

    async def _handle_start(self, info: _BotInfo, chat: dict, match: re.Match) -> None:
        mention, code = match[2], match[3]
        if mention and mention.casefold() != info.bot.username.casefold():
            return  # в группе команда другому боту
        chat_id = chat["id"]
        async with self._deps.sessionmaker() as session:
            row = (
                await session.execute(
                    select(BotChat).where(BotChat.bot_id == self._bot_id, BotChat.chat_id == chat_id)
                )
            ).scalar_one_or_none()
            # байты: compare_digest не принимает строки не из ASCII, а код присылает кто угодно
            if code and secrets.compare_digest(code.encode(), info.bot.invite_code.encode()):
                if row is None:
                    row = BotChat(bot_id=self._bot_id, chat_id=chat_id)
                    session.add(row)
                row.title = _chat_title(chat)
                row.username = chat.get("username")
                await session.commit()
                text = f"Готово, присылайте документы. Компания: «{info.tenant_name}»"
            else:
                # чат со старой ссылкой остаётся подключённым: отказ только ввёл бы в заблуждение
                text = ASK_FOR_LINK if row is None else ALREADY_LINKED
        await self._reply(chat_id, text)

    async def _is_linked(self, chat_id: int) -> bool:
        async with self._deps.sessionmaker() as session:
            found = await session.scalar(
                select(BotChat.id).where(BotChat.bot_id == self._bot_id, BotChat.chat_id == chat_id)
            )
        return found is not None

    async def _accept(self, info: _BotInfo, messages: list[dict], media_group_id: str | None) -> bool:
        """Сообщение или собранный альбом. False — скачивание сорвалось и его надо повторить."""
        chat_id = messages[0]["chat"]["id"]
        message_id = messages[0]["message_id"]
        # сбой БД здесь — сбой опроса: сообщение не теряем, а в чат, про который ничего не знаем, не пишем
        if not await self._is_linked(chat_id):
            return True
        suffix = f"album:{media_group_id}" if media_group_id is not None else str(message_id)
        key = f"tg:{self._bot_id}:{chat_id}:{suffix}"
        try:
            text = await self._accept_files(info, messages, key, media_group_id)
        except BotTokenRevoked:
            raise
        except BotFileTooBig:
            text = self._too_big_text()
        except BotApiError as exc:
            logger.warning("бот %s: не скачался файл: %s", self._bot_id, type(exc).__name__)
            if exc.retryable:
                attempts = self._attempts.get(key, 0) + 1
                if attempts < DOWNLOAD_RETRIES:
                    self._attempts[key] = attempts
                    self._retrying = True
                    return False
            text = DOWNLOAD_FAILED
        except IntakeError as exc:
            text = exc.message
        except Exception as exc:
            # одно сообщение не должно заклинить бота
            logger.warning("бот %s: не принят документ: %s", self._bot_id, type(exc).__name__)
            text = ACCEPT_FAILED
        self._attempts.pop(key, None)
        if text:
            await self._reply(chat_id, text, message_id)
        return True

    async def _accept_files(
        self, info: _BotInfo, messages: list[dict], key: str, media_group_id: str | None
    ) -> str | None:
        """Текст ответа в чат; None — отвечать нечего."""
        files = [f for f in map(_pick_file, messages) if f is not None]
        if not files:
            return NEED_FILE if _wants_hint(messages) else None
        if any(f.size is not None and f.size > self._size_limit_mb() * MB for f in files):
            return self._too_big_text()
        caption = next((m["caption"] for m in messages if (m.get("caption") or "").strip()), None)
        tenant_id = info.bot.tenant_id
        async with self._deps.sessionmaker() as session:
            types = await list_doc_types(session, tenant_id)
            result = await accept_document(
                session,
                self._deps.intake,
                tenant_id=tenant_id,
                schema_slug=match_doc_type(caption, types) or info.bot.default_schema or AUTO_SLUG,
                files=[self._intake_file(session, f) for f in files],
                mode=SCAN_MODE,
                idempotency_key=key,
                source="telegram",
                source_ref={
                    "bot_id": str(self._bot_id),
                    "chat_id": messages[0]["chat"]["id"],
                    "message_id": messages[0]["message_id"],
                    "media_group_id": media_group_id,
                },
            )
        if result.created:
            return f"Принял, распознаю ({result.document.pages} стр.)"
        if media_group_id is not None:
            await self._report_late_parts(messages, result.document, len(files))
        return None

    async def _report_late_parts(self, messages: list[dict], document: Document, file_count: int) -> None:
        """Документ альбома уже есть. Его первое сообщение в буфере — повторная выдача альбома, иначе части опоздали."""
        first = (document.source_ref or {}).get("message_id")
        if first in (m["message_id"] for m in messages):
            # тот же альбом после перезапуска; какие части лишние, по документу не узнать
            if file_count > len(document.files):
                logger.warning("бот %s: при повторной выдаче альбома есть лишние части", self._bot_id)
            return
        for message in messages:
            if _pick_file(message) is not None:
                await self._reply(message["chat"]["id"], LATE_ALBUM_PART, message["message_id"])

    def _intake_file(self, session: AsyncSession, file: _File) -> IntakeFile:
        async def read(limit: int) -> bytes:
            # accept_document читает файлы посреди своей сессии; документа в ней ещё нет, а соединение на время
            # скачивания надо отпустить
            await session.commit()
            return await self._api.download(file.file_id, min(limit, MAX_DOWNLOAD_BYTES))

        return IntakeFile(file.name, read)

    def _size_limit_mb(self) -> int:
        return min(MAX_DOWNLOAD_BYTES // MB, self._deps.settings.max_file_mb)

    def _too_big_text(self) -> str:
        return f"Файл больше {self._size_limit_mb()} МБ: Telegram не отдаёт боту такие файлы, загрузите его в кабинете"

    async def _reply(self, chat_id: int, text: str, reply_to: int | None = None) -> None:
        try:
            await self._api.send_message(chat_id, text, reply_to)
        except Exception as exc:
            logger.warning("бот %s: не отправлен ответ: %s", self._bot_id, type(exc).__name__)
