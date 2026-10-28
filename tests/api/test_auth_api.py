import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from docflow.auth import hash_password
from docflow.db.models import Session, Tenant, User

PASSWORD = "correct-horse-1"


@pytest.fixture
async def user(api):
    async with api.sessionmaker() as s:
        tenant = Tenant(name="Ромашка")
        s.add(tenant)
        await s.flush()
        u = User(tenant_id=tenant.id, email="anna@example.com", password_hash=hash_password(PASSWORD))
        s.add(u)
        await s.commit()
        return u.id


async def _login(api, email="anna@example.com", password=PASSWORD, headers=None):
    return await api.client.post("/v1/auth/login", json={"email": email, "password": password}, headers=headers)


def _bearer(token):
    return {"Authorization": f"Bearer {token}"}


async def test_login_ok_and_me(api, user):
    r = await _login(api)
    assert r.status_code == 200
    body = r.json()
    assert body["token"].startswith("dfs_")
    assert body["user"]["email"] == "anna@example.com" and body["user"]["role"] == "owner"
    me = await api.client.get("/v1/me", headers=_bearer(body["token"]))
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "anna@example.com"
    assert me.json()["tenant"]["name"] == "Ромашка"


async def test_login_wrong_password_and_unknown_email_same_401(api, user):
    a = await _login(api, password="wrong-password-1")
    b = await _login(api, email="nobody@example.com")
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json() == {"detail": "неверный email или пароль"}


async def test_login_email_case_insensitive(api, user):
    r = await _login(api, email="  Anna@Example.COM ")
    assert r.status_code == 200


async def test_login_rate_limited(api, user):
    for _ in range(5):
        assert (await _login(api, password="wrong-password-1")).status_code == 401
    r = await _login(api, password="wrong-password-1")
    assert r.status_code == 429
    assert r.json() == {"detail": "слишком много попыток, попробуйте через 15 минут"}
    assert (await _login(api)).status_code == 429  # даже с верным паролем


async def test_login_rate_limit_holds_under_concurrency(api, user):
    results = await asyncio.gather(*[_login(api, password="wrong-password-1") for _ in range(10)])
    codes = [r.status_code for r in results]
    assert codes.count(401) == 5 and codes.count(429) == 5


async def test_successful_login_possible_within_first_attempts(api, user):
    for _ in range(4):
        assert (await _login(api, password="wrong-password-1")).status_code == 401
    assert (await _login(api)).status_code == 200


async def test_logout_revokes(api, user):
    token = (await _login(api)).json()["token"]
    assert (await api.client.post("/v1/auth/logout", headers=_bearer(token))).status_code == 204
    assert (await api.client.get("/v1/me", headers=_bearer(token))).status_code == 401


async def test_logout_with_api_key_is_400(api, auth):
    assert (await api.client.post("/v1/auth/logout", headers=auth)).status_code == 400


async def test_expired_session_401(api, user):
    token = (await _login(api)).json()["token"]
    async with api.sessionmaker() as s:
        row = (await s.execute(select(Session))).scalar_one()
        row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        await s.commit()
    r = await api.client.get("/v1/me", headers=_bearer(token))
    assert r.status_code == 401


async def test_disabled_user_401(api, user):
    token = (await _login(api)).json()["token"]
    async with api.sessionmaker() as s:
        (await s.get(User, user)).disabled_at = datetime.now(UTC)
        await s.commit()
    assert (await api.client.get("/v1/me", headers=_bearer(token))).status_code == 401
    assert (await _login(api)).status_code == 401  # и войти отключённый не может


async def test_session_reaches_existing_endpoints(api, user):
    token = (await _login(api)).json()["token"]
    assert (await api.client.get("/v1/webhooks", headers=_bearer(token))).status_code == 200


async def test_bad_token_keeps_old_message(api):
    r = await api.client.get("/v1/webhooks", headers=_bearer("dfs_nope"))
    assert r.status_code == 401 and r.json()["detail"] == "неверный или отозванный ключ API"


async def test_api_key_me_has_null_user(api, auth):
    r = await api.client.get("/v1/me", headers=auth)
    assert r.status_code == 200
    assert r.json()["user"] is None and r.json()["tenant"]["name"] == "Ромашка"


async def test_client_ip_header_trusted_only_with_internal_token(api, user):
    # без internal_token заголовок игнорируется: все попытки считаются на одном IP (адрес тестового клиента)
    for i in range(20):
        r = await _login(api, email=f"u{i}@example.com", headers={"X-Client-IP": f"10.0.0.{i}"})
        assert r.status_code == 401
    r = await _login(api, email="u99@example.com", headers={"X-Client-IP": "10.9.9.9"})
    assert r.status_code == 429


async def test_client_ip_header_used_with_internal_token(api, user):
    api.settings.internal_token = "secret-internal"
    trusted = {"X-Internal-Token": "secret-internal", "X-Client-IP": "10.0.0.1"}
    for i in range(20):
        assert (await _login(api, email=f"u{i}@example.com", headers=trusted)).status_code == 401
    assert (await _login(api, email="x@example.com", headers=trusted)).status_code == 429
    other = {"X-Internal-Token": "secret-internal", "X-Client-IP": "10.0.0.2"}
    assert (await _login(api, email="y@example.com", headers=other)).status_code == 401
    # неверный токен: заголовок игнорируется, берётся адрес клиента (другой счётчик)
    wrong = {"X-Internal-Token": "wrong", "X-Client-IP": "10.0.0.1"}
    assert (await _login(api, email="z@example.com", headers=wrong)).status_code == 401
