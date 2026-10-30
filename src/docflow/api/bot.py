"""Свой Telegram-бот компании: владелец вставляет токен от @BotFather, остальное делает канальный процесс.

Токен принимается, шифруется и никогда не возвращается; в ответах только имя бота и ссылки-приглашения.
Управляет подключением только владелец (сессия): ключу API и оператору — 403.
"""

import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, ConfigDict, StrictBool
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError

from docflow.api.deps import Principal, SessionDep, SettingsDep, parse_id, require_owner
from docflow.channels.botapi import TOKEN_RE, BotApi, BotApiError, BotTokenRevoked
from docflow.db.models import BotChat, TenantBot, hash_key
from docflow.schemas.catalog import AUTO_SLUG, list_doc_types
from docflow.secrets import SecretsKeyMissing, encrypt_secrets

router = APIRouter(prefix="/v1/bot", tags=["bot"])

OwnerDep = Annotated[Principal, Depends(require_owner)]
logger = logging.getLogger(__name__)

TOKEN_REJECTED = "Telegram не принял токен"
ALREADY_CONNECTED = "этот бот уже подключён к другому кабинету"
DISCONNECT_FIRST = "Сначала отключите текущего бота"
TELEGRAM_DOWN = "Telegram не отвечает, попробуйте позже"
NO_BOT = "бот не подключён"
NO_CHAT = "чат не найден"
HTTP_TIMEOUT = 20.0


async def get_bot_http(request: Request) -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient(transport=request.app.state.bot_transport, timeout=HTTP_TIMEOUT) as http:
        yield http


HttpDep = Annotated[httpx.AsyncClient, Depends(get_bot_http)]


class BotIn(BaseModel):
    # без ограничений pydantic: 422 по длине или формату вернул бы токен в input; формат проверяем руками
    model_config = ConfigDict(extra="forbid")
    token: str


class BotPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    default_schema: str | None


class ChatPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    all_documents: StrictBool


def _error(code: int, message: str) -> HTTPException:
    return HTTPException(code, message)


def _chat_out(c: BotChat) -> dict:
    return {
        "id": str(c.id),
        "title": c.title,
        "username": c.username,
        "all_documents": c.all_documents,
        "linked_at": c.linked_at.isoformat() if c.linked_at else None,
    }


async def _out(session, bot: TenantBot) -> dict:
    chats = (
        (await session.execute(select(BotChat).where(BotChat.bot_id == bot.id).order_by(BotChat.linked_at, BotChat.id)))
        .scalars()
        .all()
    )
    base = f"https://t.me/{bot.username}"
    return {
        "username": bot.username,
        "active": bot.active,
        "last_error": bot.last_error,
        "default_schema": bot.default_schema,
        "invite_link": f"{base}?start={bot.invite_code}",
        "group_invite_link": f"{base}?startgroup={bot.invite_code}",
        "chats": [_chat_out(c) for c in chats],
    }


async def _bot_of(session, tenant_id: uuid.UUID) -> TenantBot | None:
    return (await session.execute(select(TenantBot).where(TenantBot.tenant_id == tenant_id))).scalar_one_or_none()


async def _require_bot(session, tenant_id: uuid.UUID) -> TenantBot:
    bot = await _bot_of(session, tenant_id)
    if bot is None:
        raise _error(status.HTTP_404_NOT_FOUND, NO_BOT)
    return bot


@router.get("", summary="Подключённый Telegram-бот")
async def get_bot(principal: OwnerDep, session: SessionDep) -> dict:
    bot = await _bot_of(session, principal.tenant_id)
    return {"bot": await _out(session, bot) if bot else None}


@router.post("", status_code=status.HTTP_201_CREATED, summary="Подключить бота по токену")
async def connect_bot(
    body: BotIn, principal: OwnerDep, session: SessionDep, settings: SettingsDep, http: HttpDep
) -> dict:
    token = body.token.strip()
    if not TOKEN_RE.match(token):
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, TOKEN_REJECTED)
    if await _bot_of(session, principal.tenant_id) is not None:
        raise _error(status.HTTP_409_CONFLICT, DISCONNECT_FIRST)
    token_hash = hash_key(token)
    if (await session.execute(select(TenantBot.id).where(TenantBot.token_hash == token_hash))).first() is not None:
        raise _error(status.HTTP_409_CONFLICT, ALREADY_CONNECTED)
    try:
        # шифруем до обращения к Telegram: без ключа не трогаем вебхук чужого бота зря
        token_enc = encrypt_secrets({"token": token}, settings.secrets_key)
    except SecretsKeyMissing as exc:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from None
    api = BotApi(http, token, settings.telegram_api_url)
    try:
        me = await api.get_me()
        await api.delete_webhook()
    except BotTokenRevoked:
        raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, TOKEN_REJECTED) from None
    except BotApiError as exc:
        logger.warning("подключение бота: %s", exc.message)
        raise _error(status.HTTP_502_BAD_GATEWAY, TELEGRAM_DOWN) from None
    username = me.get("username") if isinstance(me, dict) else None
    if not isinstance(username, str) or not username:
        raise _error(status.HTTP_502_BAD_GATEWAY, TELEGRAM_DOWN)
    bot = TenantBot(
        tenant_id=principal.tenant_id,
        token_enc=token_enc,
        token_hash=token_hash,
        username=username,
        invite_code=secrets.token_urlsafe(16),
    )
    session.add(bot)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise _error(status.HTTP_409_CONFLICT, ALREADY_CONNECTED) from None
    return await _out(session, bot)


@router.patch("", summary="Тип документов по умолчанию")
async def patch_bot(body: BotPatch, principal: OwnerDep, session: SessionDep) -> dict:
    bot = await _require_bot(session, principal.tenant_id)
    slug = None if body.default_schema in (None, AUTO_SLUG) else body.default_schema
    if slug is not None:
        known = {t.slug for t in await list_doc_types(session, principal.tenant_id)}
        if slug not in known:
            raise _error(status.HTTP_422_UNPROCESSABLE_CONTENT, "неизвестный тип документа")
    bot.default_schema = slug
    await session.commit()
    return await _out(session, bot)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT, summary="Отключить бота")
async def disconnect_bot(principal: OwnerDep, session: SessionDep) -> Response:
    bot = await _require_bot(session, principal.tenant_id)
    await session.execute(delete(BotChat).where(BotChat.bot_id == bot.id))
    await session.delete(bot)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/invite", summary="Выпустить новую пригласительную ссылку")
async def rotate_invite(principal: OwnerDep, session: SessionDep) -> dict:
    bot = await _require_bot(session, principal.tenant_id)
    bot.invite_code = secrets.token_urlsafe(16)
    await session.commit()
    return await _out(session, bot)


async def _own_chat(session, tenant_id: uuid.UUID, chat_id: str) -> BotChat:
    chat = (
        await session.execute(
            select(BotChat)
            .join(TenantBot, TenantBot.id == BotChat.bot_id)
            .where(BotChat.id == parse_id(chat_id), TenantBot.tenant_id == tenant_id)
        )
    ).scalar_one_or_none()
    if chat is None:
        raise _error(status.HTTP_404_NOT_FOUND, NO_CHAT)
    return chat


@router.patch("/chats/{chat_id}", summary="Присылать ли в чат результаты всех документов")
async def patch_chat(chat_id: str, body: ChatPatch, principal: OwnerDep, session: SessionDep) -> dict:
    chat = await _own_chat(session, principal.tenant_id, chat_id)
    chat.all_documents = body.all_documents
    await session.commit()
    return _chat_out(chat)


@router.delete("/chats/{chat_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Отвязать чат")
async def unlink_chat(chat_id: str, principal: OwnerDep, session: SessionDep) -> Response:
    chat = await _own_chat(session, principal.tenant_id, chat_id)
    await session.delete(chat)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
