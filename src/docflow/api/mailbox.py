"""Почтовый ящик компании: подключение по IMAP для приёма документов.

Пароль принимается, шифруется и никогда не возвращается. Управляет ящиком только владелец (сессия): ключу API и
оператору — 403.
"""

import asyncio
import logging
import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from docflow.api.deps import Principal, SessionDep, SettingsDep, require_owner
from docflow.channels.mailbox import (
    AUTH_ERROR,
    HOST_REFUSED,
    NETWORK_ERROR,
    SECRETS_ERROR,
    MailboxAuthError,
    MailboxCreds,
    check_host,
)
from docflow.db.models import Mailbox
from docflow.schemas.catalog import AUTO_SLUG, list_doc_types
from docflow.secrets import SecretsKeyMissing, decrypt_secrets, encrypt_secrets
from docflow.webhooks import UnresolvableHost

router = APIRouter(prefix="/v1/mailbox", tags=["mailbox"])

OwnerDep = Annotated[Principal, Depends(require_owner)]
logger = logging.getLogger(__name__)

NO_MAILBOX = "почта не подключена"
HOST_RE = re.compile(r"[A-Za-z0-9.-]{1,253}")
SENDER_RE = re.compile(r"[^\s@]*@[^\s@.]+(?:\.[^\s@.]+)+")
CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
BAD_FOLDER_RE = re.compile(r'["\\\x00-\x1f\x7f]')


class MailboxIn(BaseModel):
    # без ограничений pydantic: длину и формат проверяем руками, текст ошибки не содержит значения
    model_config = ConfigDict(extra="forbid")
    host: str
    port: int = 993
    username: str
    password: str | None = None
    folder: str = "INBOX"
    allowed_senders: list[str] = []
    default_schema: str | None = None


def _unprocessable(message: str) -> HTTPException:
    return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, message)


def _out(box: Mailbox) -> dict:
    return {
        "host": box.host,
        "port": box.port,
        "username": box.username,
        "folder": box.folder,
        "allowed_senders": box.allowed_senders,
        "default_schema": box.default_schema,
        "active": box.active,
        "last_error": box.last_error,
        "last_checked_at": box.last_checked_at.isoformat() if box.last_checked_at else None,
    }


def _clean_senders(senders: list[str]) -> list[str]:
    cleaned = [s.strip().lower() for s in senders]
    # NUL и прочие управляющие символы не примет JSONB
    if not all(SENDER_RE.fullmatch(s) and not CONTROL_RE.search(s) for s in cleaned):
        raise _unprocessable("Разрешённые отправители — адреса вида имя@домен или домены вида @домен")
    return cleaned


def _check_fields(body: MailboxIn) -> None:
    # fullmatch: $ пропустил бы хост с переводом строки в конце
    if not HOST_RE.fullmatch(body.host):
        raise _unprocessable("Адрес почтового сервера указан неверно")
    if not 1 <= body.port <= 65535:
        raise _unprocessable("Порт должен быть от 1 до 65535")
    # imaplib подставляет логин в команду как есть: перевод строки добавил бы к ней свою
    if not body.username or CONTROL_RE.search(body.username):
        raise _unprocessable("Логин указан неверно")
    if body.password and CONTROL_RE.search(body.password):
        raise _unprocessable("Пароль содержит недопустимые символы")
    if not body.folder or BAD_FOLDER_RE.search(body.folder):
        raise _unprocessable("Название папки указано неверно")
    # imaplib шлёт команды в ASCII, а кодировку имён папок IMAP (modified UTF-7) мы не поддерживаем
    if not body.folder.isascii():
        raise _unprocessable("Папка должна быть указана латиницей, как на почтовом сервере")


async def _mailbox_of(session, tenant_id: uuid.UUID) -> Mailbox | None:
    return (await session.execute(select(Mailbox).where(Mailbox.tenant_id == tenant_id))).scalar_one_or_none()


async def _require_mailbox(session, tenant_id: uuid.UUID) -> Mailbox:
    box = await _mailbox_of(session, tenant_id)
    if box is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, NO_MAILBOX)
    return box


@router.get("", summary="Подключённый почтовый ящик")
async def get_mailbox(principal: OwnerDep, session: SessionDep) -> dict:
    box = await _mailbox_of(session, principal.tenant_id)
    return {"mailbox": _out(box) if box else None}


@router.put("", summary="Подключить ящик или изменить настройки")
async def put_mailbox(body: MailboxIn, principal: OwnerDep, session: SessionDep, settings: SettingsDep) -> dict:
    body = body.model_copy(update={"host": body.host.strip(), "username": body.username.strip()})
    _check_fields(body)
    senders = _clean_senders(body.allowed_senders)
    slug = None if body.default_schema in (None, AUTO_SLUG) else body.default_schema
    if slug is not None:
        known = {t.slug for t in await list_doc_types(session, principal.tenant_id)}
        if slug not in known:
            raise _unprocessable("неизвестный тип документа")
    box = await _mailbox_of(session, principal.tenant_id)
    if box is None and not body.password:
        raise _unprocessable("Укажите пароль")
    # сохранённый пароль нельзя отправить на другой сервер: иначе его заберёт любой, кто вошёл в кабинет
    if box is not None and not body.password and (body.host, body.port) != (box.host, box.port):
        raise _unprocessable("Укажите пароль: адрес сервера изменился")
    password_enc = None
    if body.password:
        try:
            password_enc = encrypt_secrets({"password": body.password}, settings.secrets_key)
        except SecretsKeyMissing as exc:
            raise _unprocessable(str(exc)) from None
    # getaddrinfo блокирующий и может идти долго: в отдельном потоке и без соединения с БД
    await session.commit()
    try:
        await asyncio.to_thread(check_host, body.host, body.port, allow_private=settings.webhook_allow_private)
    except UnresolvableHost:
        raise _unprocessable("Почтовый сервер не найден, проверьте адрес") from None
    except ValueError:
        raise _unprocessable(HOST_REFUSED) from None
    if box is None:
        box = Mailbox(tenant_id=principal.tenant_id, password_enc=password_enc)
        session.add(box)
    elif password_enc is not None:
        box.password_enc = password_enc
    box.host = body.host
    box.port = body.port
    box.username = body.username
    box.folder = body.folder
    box.allowed_senders = senders
    box.default_schema = slug
    box.active = True
    box.last_error = None
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Ящик уже подключается, обновите страницу") from None
    return _out(box)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Отключить ящик")
async def delete_mailbox(principal: OwnerDep, session: SessionDep) -> Response:
    box = await _require_mailbox(session, principal.tenant_id)
    await session.delete(box)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _failure(message: str) -> dict:
    return {"ok": False, "message": message, "unseen": None}


@router.post("/test", summary="Проверить подключение к ящику")
async def check_mailbox(request: Request, principal: OwnerDep, session: SessionDep, settings: SettingsDep) -> dict:
    box = await _require_mailbox(session, principal.tenant_id)
    host, port, username, folder = box.host, box.port, box.username, box.folder
    password_enc = box.password_enc
    # проверка ходит в сеть до 20 секунд: соединение с БД на это время отпускаем
    await session.commit()
    try:
        await asyncio.to_thread(check_host, host, port, allow_private=settings.webhook_allow_private)
    except UnresolvableHost:
        return _failure(NETWORK_ERROR)
    except ValueError:
        return _failure(HOST_REFUSED)
    try:
        password = decrypt_secrets(password_enc, settings.secrets_key)["password"]
    except SecretsKeyMissing:
        return _failure(SECRETS_ERROR)
    connect = request.app.state.imap_connect
    try:
        mail = await asyncio.to_thread(connect, MailboxCreds(host, port, username, password, folder))
    except MailboxAuthError:
        return _failure(AUTH_ERROR)
    except Exception as exc:
        logger.warning("проверка почты: сбой подключения: %s", type(exc).__name__)
        return _failure(NETWORK_ERROR)
    try:
        unseen = len(await asyncio.to_thread(mail.unseen_uids))
    except Exception as exc:
        logger.warning("проверка почты: не получен список писем: %s", type(exc).__name__)
        return _failure(NETWORK_ERROR)
    finally:
        await asyncio.to_thread(mail.close)
    return {"ok": True, "message": f"Подключение работает, непрочитанных писем: {unseen}", "unseen": unseen}
