"""Вход людей в кабинет: логин по паролю, выход, «кто я»."""

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from docflow.api.deps import PrincipalDep, SessionDep, SettingsDep
from docflow.api.ratelimit import client_ip
from docflow.auth import SESSION_TTL, dummy_hash, generate_session_token, verify_password
from docflow.db.models import Session as UserSession
from docflow.db.models import Tenant, User

router = APIRouter(tags=["auth"])

BAD_LOGIN = "неверный email или пароль"
TOO_MANY = "слишком много попыток, попробуйте через 15 минут"


class LoginIn(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=256)


def _public_user(user: User) -> dict[str, Any]:
    return {"id": str(user.id), "email": user.email, "role": user.role}


@router.post(
    "/v1/auth/login",
    summary="Войти по email и паролю",
    description="Возвращает токен сессии `dfs_…` на 30 дней. После 5 неудачных попыток подряд — 429 на 15 минут.",
)
async def login(body: LoginIn, request: Request, session: SessionDep, settings: SettingsDep) -> dict[str, Any]:
    email = body.email.strip().lower()
    email_key, ip_key = f"email:{email}", f"ip:{client_ip(request, settings)}"
    email_limiter, ip_limiter = request.app.state.login_email_limiter, request.app.state.login_ip_limiter
    # слот занимаем сразу и без await между проверкой и записью: параллельные запросы не обходят лимит.
    # Проверка идёт до пароля: перебирающему не говорим, угадал ли он
    if not email_limiter.hit(email_key) or not ip_limiter.hit(ip_key):
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, TOO_MANY)

    user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
    # для неизвестного email всё равно считаем хеш: по времени ответа не видно, есть ли такой пользователь;
    # argon2 грузит процессор десятки миллисекунд — в пул потоков, чтобы не вешать цикл событий
    password_ok = await run_in_threadpool(verify_password, user.password_hash if user else dummy_hash(), body.password)
    if user is None or not password_ok or user.disabled_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, BAD_LOGIN)

    email_limiter.reset(email_key)
    token, token_hash = generate_session_token()
    expires_at = datetime.now(UTC) + SESSION_TTL
    session.add(UserSession(user_id=user.id, token_hash=token_hash, expires_at=expires_at))
    await session.commit()
    return {"token": token, "expires_at": expires_at.isoformat(), "user": _public_user(user)}


@router.post("/v1/auth/logout", status_code=status.HTTP_204_NO_CONTENT, summary="Выйти (отозвать сессию)")
async def logout(principal: PrincipalDep, session: SessionDep) -> Response:
    if principal.session_id is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "выход нужен только для сессии, а не для ключа API")
    row = await session.get(UserSession, principal.session_id)
    if row is not None:
        row.revoked_at = datetime.now(UTC)
        await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/v1/me", summary="Кто я и какой клиент")
async def me(principal: PrincipalDep, session: SessionDep) -> dict[str, Any]:
    tenant = await session.get(Tenant, principal.tenant_id)
    user = await session.get(User, principal.user_id) if principal.user_id else None
    return {
        "user": _public_user(user) if user else None,
        "tenant": {
            "id": str(tenant.id),
            "name": tenant.name,
            "extractor": tenant.extractor,
            "auto_ok": tenant.auto_ok,
        },
    }
