"""Приём документов из почтового ящика клиента по IMAP.

Ни одна транзакция не живёт дольше обращения к БД: IMAP идёт в отдельном потоке и без открытого соединения.
В лог и last_error не попадает текст исключений imaplib: он может повторять команду LOGIN вместе с паролем.
"""

import asyncio
import contextlib
import email
import hashlib
import imaplib
import logging
import re
import ssl
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email import policy
from email.message import EmailMessage
from typing import Protocol
from uuid import UUID

from sqlalchemy import update

from docflow.channels.deps import ChannelDeps
from docflow.connectors.notify_common import one_line
from docflow.db.models import Mailbox
from docflow.imaging.load import detect_kind
from docflow.intake import (
    IntakeError,
    IntakeFile,
    IntakeLimitExceeded,
    IntakeStorageFailed,
    accept_document,
    request_limit_bytes,
)
from docflow.schemas.catalog import AUTO_SLUG, list_doc_types, match_doc_type
from docflow.secrets import SecretsKeyMissing, decrypt_secrets
from docflow.webhooks import UnresolvableHost, check_webhook_url

logger = logging.getLogger(__name__)

MAX_LETTERS_PER_PASS = 20
IMAP_TIMEOUT = 20
MAX_ERROR = 500
MAX_LETTER_ATTEMPTS = 3

AUTH_ERROR = "Почта не приняла логин или пароль"
NETWORK_ERROR = "Не удалось подключиться к почте"
HOST_REFUSED = "Адрес почтового сервера не разрешён: он должен быть публичным"
SECRETS_ERROR = "Не удалось расшифровать пароль почты: проверьте ключ DOCFLOW_SECRETS_KEY на сервере"
ACCEPT_FAILED = "не удалось принять документ, повторим при следующей проверке"
ACCEPT_GAVE_UP = "не удалось принять документ, письмо пропущено и осталось непрочитанным"
LETTER_GAVE_UP = "Почта не отдаёт письмо: после нескольких попыток оно пропущено и осталось непрочитанным"
PARSE_FAILED = "не удалось разобрать письмо, оно пропущено и осталось непрочитанным"
# ошибки подключения: их снимает удачный вход, а ошибки писем остаются на виду, пока письмо не пройдёт
CONNECTION_ERRORS = (NETWORK_ERROR, HOST_REFUSED, SECRETS_ERROR)

_SUBJECT_PREFIXES = re.compile(r"^(?:\s*(?:re|fwd?|ответ|пересл)\s*:)+\s*", re.IGNORECASE)
_SIZE = re.compile(rb"RFC822\.SIZE (\d+)")


class MailboxAuthError(Exception):
    """Почта не приняла логин или пароль. Текст постоянный, без пароля."""


class MailboxNetworkError(Exception):
    """Соединение не установлено или оборвалось. В тексте только тип исключения."""


class MailboxLetterError(Exception):
    """Сервер не отдал одно письмо (удалено, отказ по команде); соединение живо."""


@dataclass(frozen=True)
class MailboxCreds:
    host: str
    port: int
    username: str
    password: str = field(repr=False)
    folder: str


class MailSession(Protocol):
    def unseen_uids(self) -> list[str]: ...
    def size(self, uid: str) -> int: ...
    def subject(self, uid: str) -> str: ...  # только заголовок: для письма, которое слишком велико, чтобы качать
    def fetch(self, uid: str) -> bytes: ...  # BODY.PEEK[]: прочитанным не помечает
    def mark_seen(self, uid: str) -> None: ...
    def close(self) -> None: ...


class _ImapSession:
    def __init__(self, conn) -> None:
        self._conn = conn

    def _uid(self, *command) -> list:
        try:
            status, data = self._conn.uid(*command)
        except (imaplib.IMAP4.abort, OSError, EOFError) as exc:
            raise MailboxNetworkError(type(exc).__name__) from None
        except (imaplib.IMAP4.error, ValueError) as exc:
            raise MailboxLetterError(type(exc).__name__) from None
        if status != "OK":
            raise MailboxLetterError(status)
        return data

    def _literal(self, uid: str, what: str) -> bytes:
        for item in self._uid("FETCH", uid, what):
            if isinstance(item, tuple):
                return item[1]
        raise MailboxLetterError("нет данных")

    def unseen_uids(self) -> list[str]:
        data = self._uid("SEARCH", None, "UNSEEN")
        return [uid.decode() for uid in (data[0] or b"").split()] if data else []

    def size(self, uid: str) -> int:
        for item in self._uid("FETCH", uid, "(RFC822.SIZE)"):
            found = _SIZE.search(item) if isinstance(item, bytes) else None
            if found:
                return int(found[1])
        raise MailboxLetterError("нет данных")

    def subject(self, uid: str) -> str:
        return _parse_subject(self._literal(uid, "(BODY.PEEK[HEADER.FIELDS (SUBJECT)])"))

    def fetch(self, uid: str) -> bytes:
        return self._literal(uid, "(BODY.PEEK[])")

    def mark_seen(self, uid: str) -> None:
        self._uid("STORE", uid, "+FLAGS", "(\\Seen)")

    def close(self) -> None:
        with contextlib.suppress(Exception):
            self._conn.logout()


def open_imap(creds: MailboxCreds, *, factory=imaplib.IMAP4_SSL) -> MailSession:
    """Вход и выбор папки. Текст исключений imaplib наружу не идёт: он может содержать команду с паролем."""
    # imaplib по умолчанию сертификат не проверяет
    context = ssl.create_default_context()
    try:
        conn = factory(creds.host, creds.port, ssl_context=context, timeout=IMAP_TIMEOUT)
    except Exception as exc:
        raise MailboxNetworkError(type(exc).__name__) from None
    session = _ImapSession(conn)
    try:
        conn.login(creds.username, creds.password)
    except (imaplib.IMAP4.abort, OSError, EOFError) as exc:  # abort — наследник error, поэтому раньше него
        session.close()
        raise MailboxNetworkError(type(exc).__name__) from None
    except (imaplib.IMAP4.error, UnicodeError):  # imaplib кодирует команду в ASCII: такой пароль сервер не получит
        session.close()
        raise MailboxAuthError(AUTH_ERROR) from None
    try:
        status, _ = conn.select(f'"{creds.folder}"')
    except Exception as exc:
        session.close()
        raise MailboxNetworkError(type(exc).__name__) from None
    if status != "OK":
        session.close()
        raise MailboxNetworkError(status)
    return session


@dataclass(frozen=True)
class Attachment:
    name: str
    data: bytes


@dataclass(frozen=True)
class Letter:
    message_id: str | None
    sender: str
    subject: str
    pdfs: list[Attachment]
    images: list[Attachment]


def _safe_text(value: object) -> str:
    """Текст письма в одну строку, пригодный для БД: Postgres не примет ни NUL, ни суррогаты от битых байтов."""
    return one_line(value).encode("utf-8", "replace").decode()


def _header(msg: EmailMessage, name: str) -> str:
    # разбор заголовка ленивый: на испорченном значении парсер падает при обращении, а не при чтении письма
    try:
        return _safe_text(msg[name])
    except Exception as exc:
        logger.info("письмо: не разобран заголовок %s: %s", name, type(exc).__name__)
        return ""


def _parse_subject(raw: bytes) -> str:
    return _header(email.message_from_bytes(raw, policy=policy.default), "subject")


def _sender(msg: EmailMessage) -> str:
    """Адрес отправителя; "" — если он не один: письмо с двумя From не должно проходить по первому из них."""
    try:
        headers = msg.get_all("from") or []
        addresses = headers[0].addresses if len(headers) == 1 else ()
        return _safe_text(addresses[0].addr_spec).lower() if len(addresses) == 1 and addresses[0].domain else ""
    except Exception as exc:
        logger.info("письмо: не разобран отправитель: %s", type(exc).__name__)
        return ""


def _is_embedded(part: EmailMessage) -> bool:
    """Картинка внутри текста письма (логотип подписи): на неё ссылаются по Content-ID, вложением она не считается."""
    return part.get("content-id") is not None and part.get_content_disposition() != "attachment"


def parse_letter(raw: bytes) -> Letter:
    msg = email.message_from_bytes(raw, policy=policy.default)
    pdfs: list[Attachment] = []
    images: list[Attachment] = []
    for part in msg.walk():
        if part.is_multipart():
            continue
        try:
            data = part.get_payload(decode=True)
            kind = detect_kind(data) if data else None
            if kind is None or (kind == "image" and _is_embedded(part)):
                continue
            name = _safe_text(part.get_filename())
        except Exception as exc:
            logger.info("письмо: не разобрано вложение: %s", type(exc).__name__)
            continue
        if kind == "pdf":
            pdfs.append(Attachment(name or f"attachment-{len(pdfs) + 1}.pdf", data))
        else:
            images.append(Attachment(name or f"image-{len(images) + 1}", data))
    return Letter(_header(msg, "message-id") or None, _sender(msg), _header(msg, "subject"), pdfs, images)


def sender_allowed(sender: str, allowed: list[str]) -> bool:
    """Пустой список — все; "a@b.ru" — точно этот адрес, "@b.ru" — точно этот домен, без поддоменов."""
    if not allowed:
        return True
    sender = sender.lower()
    if not sender:
        return False
    return any(sender.endswith(rule) if rule.startswith("@") else sender == rule for rule in map(str.lower, allowed))


def subject_for_match(subject: str) -> str:
    return _SUBJECT_PREFIXES.sub("", subject).strip()


def check_host(host: str, port: int, *, allow_private: bool) -> None:
    """Защита от SSRF: ValueError, если хост не публичный или не резолвится (UnresolvableHost)."""
    check_webhook_url(f"https://{host}:{port}/", allow_private=allow_private)


def _letter_name(subject: str) -> str:
    return f"Письмо «{one_line(subject) or 'без темы'}»"


def _letter_error(subject: str, message: str) -> str:
    return f"{_letter_name(subject)}: {message}"


def _subject_if_known(raw: bytes) -> str:
    try:
        return _parse_subject(raw)
    except Exception:
        return ""


def _digest(letter: Letter, raw: bytes) -> str:
    return hashlib.sha256(letter.message_id.encode() if letter.message_id else raw).hexdigest()


def _settings_filter(box: Mailbox) -> tuple:
    return box.host, box.username, box.folder, tuple(box.allowed_senders or ())


class MailboxPoller:
    def __init__(self, deps: ChannelDeps, connect=open_imap):
        self._deps = deps
        self._connect = connect
        # письма, которые оставили непрочитанными: без памяти их пришлось бы качать каждую минуту
        self._skipped: dict[UUID, set[str]] = {}
        self._skipped_filter: dict[UUID, tuple] = {}
        # неудачные попытки по письму: письмо, которое не проходит раз за разом, не качаем каждую минуту вечно
        self._attempts: dict[UUID, dict[str, int]] = {}

    async def poll(self, mailbox_id: UUID) -> None:
        """Один проход. Сбои почты и отдельных писем не выбрасывает: пишет last_error."""
        box = await self._load(mailbox_id)
        if box is None:
            self._skipped.pop(mailbox_id, None)
            self._skipped_filter.pop(mailbox_id, None)
            self._attempts.pop(mailbox_id, None)
            return
        if not box.active:
            return
        session = await self._open(box)
        if session is None:
            return
        try:
            await self._read_letters(box, session)
        finally:
            with contextlib.suppress(Exception):
                await asyncio.to_thread(session.close)

    async def _load(self, mailbox_id: UUID) -> Mailbox | None:
        async with self._deps.sessionmaker() as session:
            return await session.get(Mailbox, mailbox_id)

    async def _open(self, box: Mailbox) -> MailSession | None:
        """None — войти не вышло, причина уже в last_error."""
        settings = self._deps.settings
        try:
            await asyncio.to_thread(check_host, box.host, box.port, allow_private=settings.webhook_allow_private)
        except UnresolvableHost:
            await self._save(box.id, last_error=NETWORK_ERROR, last_checked_at=datetime.now(UTC))
            return None
        except ValueError:
            await self._save(box.id, last_error=HOST_REFUSED, last_checked_at=datetime.now(UTC))
            return None
        try:
            password = decrypt_secrets(box.password_enc, settings.secrets_key)["password"]
        except SecretsKeyMissing:
            await self._save(box.id, last_error=SECRETS_ERROR, last_checked_at=datetime.now(UTC))
            return None
        creds = MailboxCreds(box.host, box.port, box.username, password, box.folder)
        try:
            session = await asyncio.to_thread(self._connect, creds)
        except MailboxAuthError:
            logger.warning("ящик %s: логин не принят", box.id)
            # пока шёл вход, владелец мог уже исправить настройки: старый отказ не должен останавливать новые
            same = (
                Mailbox.host == box.host,
                Mailbox.port == box.port,
                Mailbox.username == box.username,
                Mailbox.password_enc == box.password_enc,
            )
            await self._save(box.id, *same, active=False, last_error=AUTH_ERROR, last_checked_at=datetime.now(UTC))
            return None
        except Exception as exc:
            logger.warning("ящик %s: сбой подключения: %s", box.id, type(exc).__name__)
            await self._save(box.id, last_error=NETWORK_ERROR, last_checked_at=datetime.now(UTC))
            return None
        await self._save(box.id, Mailbox.last_error.in_(CONNECTION_ERRORS), last_error=None)
        return session

    async def _read_letters(self, box: Mailbox, session: MailSession) -> None:
        try:
            unseen = await asyncio.to_thread(session.unseen_uids)
        except Exception as exc:
            logger.warning("ящик %s: не получен список писем: %s", box.id, type(exc).__name__)
            await self._save(box.id, last_error=NETWORK_ERROR, last_checked_at=datetime.now(UTC))
            return
        skipped = self._remembered(box, unseen)
        error: str | None = None
        accepted = False
        for uid in [u for u in unseen if u not in skipped][:MAX_LETTERS_PER_PASS]:
            try:
                letter_accepted, letter_error = await self._process(box, session, uid, skipped)
            except MailboxNetworkError as exc:
                # соединение умерло: на остальных письмах каждый вызов только ждал бы таймаут
                logger.warning("ящик %s: соединение оборвалось: %s", box.id, exc)
                error = NETWORK_ERROR
                break
            except Exception as exc:
                # сервер не отдал письмо: возможно, временно, поэтому несколько попыток в следующих проходах
                logger.warning("ящик %s: письмо %s не получено: %s", box.id, uid, type(exc).__name__)
                if self._out_of_attempts(box.id, uid, skipped):
                    error = LETTER_GAVE_UP
                continue
            accepted = accepted or letter_accepted
            error = letter_error or error
        values: dict = {"last_checked_at": datetime.now(UTC)}
        # итог прохода, а не последнего письма: удачное письмо не должно стирать ошибку предыдущего
        if error is not None:
            values["last_error"] = one_line(error)[:MAX_ERROR]
        elif accepted:
            values["last_error"] = None
        await self._save(box.id, **values)

    def _remembered(self, box: Mailbox, unseen: list[str]) -> set[str]:
        """Память пропущенных писем ящика. Правка настроек её сбрасывает: чужой отправитель мог стать разрешённым."""
        current = _settings_filter(box)
        if self._skipped_filter.get(box.id) != current:
            self._skipped[box.id] = set()
            self._skipped_filter[box.id] = current
            self._attempts[box.id] = {}
        skipped = self._skipped[box.id] & set(unseen)
        self._skipped[box.id] = skipped
        attempts = self._attempts.setdefault(box.id, {})
        self._attempts[box.id] = {uid: n for uid, n in attempts.items() if uid in unseen}
        return skipped

    def _out_of_attempts(self, mailbox_id: UUID, uid: str, skipped: set[str]) -> bool:
        """Считает неудачную попытку. True — попытки кончились, письмо больше не трогаем до перезапуска."""
        attempts = self._attempts[mailbox_id]
        attempts[uid] = attempts.get(uid, 0) + 1
        if attempts[uid] < MAX_LETTER_ATTEMPTS:
            return False
        skipped.add(uid)
        return True

    async def _process(
        self, box: Mailbox, session: MailSession, uid: str, skipped: set[str]
    ) -> tuple[bool, str | None]:
        """(письмо принято целиком, текст ошибки)."""
        settings = self._deps.settings
        size = await asyncio.to_thread(session.size, uid)
        if size > request_limit_bytes(settings):
            subject = await asyncio.to_thread(session.subject, uid)
            await asyncio.to_thread(session.mark_seen, uid)
            return False, f"{_letter_name(subject)} больше {settings.max_request_mb} МБ"
        raw = await asyncio.to_thread(session.fetch, uid)
        try:
            letter = await asyncio.to_thread(parse_letter, raw)
        except Exception as exc:
            # одни и те же байты не разберутся и в следующий раз: повторять незачем
            logger.warning("ящик %s: письмо %s не разобрано: %s", box.id, uid, type(exc).__name__)
            skipped.add(uid)
            return False, _letter_error(_subject_if_known(raw), PARSE_FAILED)
        if not sender_allowed(letter.sender, box.allowed_senders or []) or not (letter.pdfs or letter.images):
            skipped.add(uid)
            return False, None
        digest = await asyncio.to_thread(_digest, letter, raw)
        error, retry, unexpected = await self._accept_letter(box, letter, digest)
        if unexpected and self._out_of_attempts(box.id, uid, skipped):
            return False, _letter_error(letter.subject, ACCEPT_GAVE_UP)
        if not retry:
            await asyncio.to_thread(session.mark_seen, uid)
        return error is None, error

    async def _accept_letter(self, box: Mailbox, letter: Letter, digest: str) -> tuple[str | None, bool, bool]:
        """(текст ошибки, письмо надо повторить, сбой неожиданный — у таких повторов есть предел).

        Сбой одного вложения не отменяет остальные: повтор отсечёт ключ.
        """
        parts = [(f"pdf:{n}", [pdf]) for n, pdf in enumerate(letter.pdfs)]
        if letter.images:
            parts.append(("images", letter.images))
        error: str | None = None
        retry = unexpected = False
        for ref, attachments in parts:
            try:
                await self._accept_part(box, letter, digest, ref, attachments)
            except IntakeLimitExceeded as exc:
                error = exc.message
            except IntakeStorageFailed as exc:
                error, retry = _letter_error(letter.subject, exc.message), True
            except IntakeError as exc:
                error = _letter_error(letter.subject, exc.message)
            except Exception as exc:
                logger.warning("ящик %s: не принят документ: %s", box.id, type(exc).__name__)
                error, retry, unexpected = _letter_error(letter.subject, ACCEPT_FAILED), True, True
        return error, retry, unexpected

    async def _accept_part(
        self, box: Mailbox, letter: Letter, digest: str, ref: str, attachments: list[Attachment]
    ) -> None:
        async with self._deps.sessionmaker() as session:
            types = await list_doc_types(session, box.tenant_id)
            await accept_document(
                session,
                self._deps.intake,
                tenant_id=box.tenant_id,
                schema_slug=match_doc_type(subject_for_match(letter.subject), types) or box.default_schema or AUTO_SLUG,
                files=[IntakeFile.of(a.name, a.data) for a in attachments],
                idempotency_key=f"mail:{box.id}:{digest}:{ref}",
                source="email",
                source_ref={"message_id": letter.message_id, "attachment": ref},
                # проход повторяет только письма без пометки; повтор при упавшем Redis плодил бы failed-документы
                keep_queued_on_enqueue_failure=True,
            )

    async def _save(self, mailbox_id: UUID, *where, **values) -> None:
        try:
            async with self._deps.sessionmaker() as session:
                await session.execute(
                    update(Mailbox)
                    .where(Mailbox.id == mailbox_id, *where)
                    .values(**values)
                    .execution_options(synchronize_session=False)
                )
                await session.commit()
        except Exception as exc:
            logger.warning("ящик %s: не удалось сохранить состояние: %s", mailbox_id, type(exc).__name__)
