import logging
import uuid

import httpx
import pytest
from api_helpers import session_for
from sqlalchemy import select, update

from docflow.channels import botapi
from docflow.db.models import BotChat, Tenant, TenantBot, hash_key
from docflow.secrets import decrypt_secrets

TOKEN = "123456:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
OTHER_TOKEN = "654321:BBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBBB"


@pytest.fixture
async def owner(api, auth):
    headers, _ = await session_for(api, auth)
    return headers


async def connect(api, headers, token=TOKEN):
    return await api.client.post("/v1/bot", headers=headers, json={"token": token})


async def bot_row(api, tenant_headers=None):
    async with api.sessionmaker() as s:
        return (await s.execute(select(TenantBot))).scalars().all()


async def add_chat(api, chat_id=-100, title="Бухгалтерия") -> uuid.UUID:
    async with api.sessionmaker() as s:
        bot = (await s.execute(select(TenantBot))).scalars().one()
        chat = BotChat(bot_id=bot.id, chat_id=chat_id, title=title, username=None)
        s.add(chat)
        await s.commit()
        return chat.id


async def test_connect_saves_encrypted_and_clears_webhook(api, owner, tg):
    r = await connect(api, owner)
    assert r.status_code == 201, r.text
    out = r.json()
    assert out["username"] == "acme_bot" and out["active"] is True and out["chats"] == []
    assert out["default_schema"] is None and out["last_error"] is None
    assert [c[0] for c in tg.calls] == ["getMe", "deleteWebhook"]
    async with api.sessionmaker() as s:
        row = (await s.execute(select(TenantBot))).scalars().one()
    assert TOKEN not in row.token_enc
    assert decrypt_secrets(row.token_enc, api.settings.secrets_key) == {"token": TOKEN}
    assert row.token_hash == hash_key(TOKEN)
    assert len(row.invite_code) >= 16
    assert out["invite_link"] == f"https://t.me/acme_bot?start={row.invite_code}"
    assert out["group_invite_link"] == f"https://t.me/acme_bot?startgroup={row.invite_code}"
    got = (await api.client.get("/v1/bot", headers=owner)).json()
    assert got == {"bot": out}


@pytest.mark.parametrize(("extractor", "expected"), [("ollama", True), ("claude", False)])
async def test_local_mode_reported(api, owner, tg, extractor, expected):
    assert (await connect(api, owner)).status_code == 201
    async with api.sessionmaker() as s:
        await s.execute(update(Tenant).values(extractor=extractor))
        await s.commit()
    assert (await api.client.get("/v1/bot", headers=owner)).json()["bot"]["local_mode"] is expected


async def test_get_without_bot(api, owner, tg):
    r = await api.client.get("/v1/bot", headers=owner)
    assert r.status_code == 200 and r.json() == {"bot": None}


@pytest.mark.parametrize("bad", ["abc", "123:a/../b", "123456:AAAA?x=1", ""])
async def test_connect_malformed_token_no_request(api, owner, tg, bad):
    r = await connect(api, owner, bad)
    assert r.status_code == 422 and r.json()["detail"] == "Telegram не принял токен"
    assert tg.calls == []


async def test_connect_token_with_whitespace_accepted(api, owner, tg):
    r = await connect(api, owner, f"  {TOKEN}\n")
    assert r.status_code == 201, r.text
    async with api.sessionmaker() as s:
        row = (await s.execute(select(TenantBot))).scalars().one()
    assert row.token_hash == hash_key(TOKEN)


async def test_connect_same_token_other_tenant_409(api, make_tenant, owner, tg):
    assert (await connect(api, owner)).status_code == 201
    other, _ = await session_for(api, await make_tenant("Вторая"))
    r = await connect(api, other)
    assert r.status_code == 409 and r.json()["detail"] == "этот бот уже подключён к другому кабинету"
    assert [c[0] for c in tg.calls] == ["getMe", "deleteWebhook"]  # второй запрос в Telegram не ходил


async def test_second_bot_same_tenant_409(api, owner, tg):
    assert (await connect(api, owner)).status_code == 201
    r = await connect(api, owner, OTHER_TOKEN)
    assert r.status_code == 409 and r.json()["detail"] == "Сначала отключите текущего бота"
    assert len(await bot_row(api)) == 1


async def test_connect_bad_token_422(api, owner, tg):
    tg.fail_with["getMe"] = 401
    r = await connect(api, owner)
    assert r.status_code == 422 and r.json()["detail"] == "Telegram не принял токен"
    assert await bot_row(api) == []
    assert [c[0] for c in tg.calls] == ["getMe"]


async def test_connect_network_502(api, owner, tg):
    tg.fail_with["getMe"] = httpx.ConnectError("boom")
    r = await connect(api, owner)
    assert r.status_code == 502 and r.json()["detail"] == "Telegram не отвечает, попробуйте позже"
    assert await bot_row(api) == []
    tg.fail_with = {"deleteWebhook": 500}
    assert (await connect(api, owner)).status_code == 502
    assert await bot_row(api) == []


async def test_no_secrets_key_422(api, owner, tg):
    api.settings.secrets_key = ""
    r = await connect(api, owner)
    assert r.status_code == 422 and "DOCFLOW_SECRETS_KEY" in r.json()["detail"]
    assert await bot_row(api) == []
    assert tg.calls == []


async def test_operator_and_api_key_403(api, auth, owner, tg):
    assert (await connect(api, owner)).status_code == 201
    chat_id = await add_chat(api)
    operator, _ = await session_for(api, auth, role="operator")
    for headers in (operator, auth):
        calls = [
            ("GET", "/v1/bot", None),
            ("POST", "/v1/bot", {"token": OTHER_TOKEN}),
            ("PATCH", "/v1/bot", {"default_schema": None}),
            ("DELETE", "/v1/bot", None),
            ("POST", "/v1/bot/invite", None),
            ("PATCH", f"/v1/bot/chats/{chat_id}", {"all_documents": False}),
            ("DELETE", f"/v1/bot/chats/{chat_id}", None),
        ]
        for method, url, payload in calls:
            r = await api.client.request(method, url, headers=headers, json=payload)
            assert r.status_code == 403, (method, url, r.status_code)
    assert len(await bot_row(api)) == 1


async def test_token_never_in_responses_or_logs(api, owner, tg, caplog):
    caplog.set_level(logging.DEBUG, logger="docflow")
    # утечка через httpx зависит от его уровня: не трогаем его, а проверяем, что он не ниже WARNING
    assert logging.getLogger("httpx").getEffectiveLevel() >= logging.WARNING
    responses = [await connect(api, owner), await api.client.get("/v1/bot", headers=owner)]
    await api.client.delete("/v1/bot", headers=owner)
    tg.fail_with["getMe"] = httpx.ConnectError(f"https://api.telegram.org/bot{TOKEN}/getMe")
    responses.append(await connect(api, owner))
    tg.fail_with["getMe"] = (500, {"ok": False, "description": f"bad {TOKEN}"})
    responses.append(await connect(api, owner))
    assert [r.status_code for r in responses] == [201, 200, 502, 502]
    for r in responses:
        assert TOKEN not in r.text and hash_key(TOKEN) not in r.text
    assert TOKEN not in caplog.text and hash_key(TOKEN) not in caplog.text
    assert botapi.logging.getLogger("httpx").level == logging.WARNING


async def test_422_does_not_echo_token(api, owner, tg):
    bodies = [
        {"token": TOKEN, "extra": TOKEN},  # лишнее поле
        {"token": [TOKEN]},  # токен не строкой
        {TOKEN: "x"},  # токен в имени ключа
        {"token": 5, "other": TOKEN},
        [TOKEN],  # тело не объект
        TOKEN,
    ]
    for payload in bodies:
        r = await api.client.post("/v1/bot", headers=owner, json=payload)
        assert r.status_code == 422, payload
        assert TOKEN not in r.text
    r = await api.client.post(
        "/v1/bot", headers={**owner, "content-type": "application/json"}, content=f'{{"token": "{TOKEN}", '.encode()
    )
    assert r.status_code == 422 and TOKEN not in r.text
    assert tg.calls == []


async def test_invite_rotation_keeps_chats(api, owner, tg):
    first = (await connect(api, owner)).json()
    await add_chat(api)
    r = await api.client.post("/v1/bot/invite", headers=owner)
    assert r.status_code == 200
    second = r.json()
    assert second["invite_link"] != first["invite_link"]
    assert second["invite_link"].startswith("https://t.me/acme_bot?start=")
    assert [c["title"] for c in second["chats"]] == ["Бухгалтерия"]


async def test_disconnect_removes_bot_and_chats(api, owner, tg):
    await connect(api, owner)
    await add_chat(api)
    assert (await api.client.delete("/v1/bot", headers=owner)).status_code == 204
    assert (await api.client.get("/v1/bot", headers=owner)).json() == {"bot": None}
    async with api.sessionmaker() as s:
        assert (await s.execute(select(BotChat))).scalars().all() == []
        assert (await s.execute(select(TenantBot))).scalars().all() == []
    assert (await api.client.delete("/v1/bot", headers=owner)).status_code == 404
    assert (await connect(api, owner)).status_code == 201  # токен снова свободен


async def test_chat_toggle_and_unlink(api, owner, tg):
    await connect(api, owner)
    chat_id = await add_chat(api)
    r = await api.client.patch(f"/v1/bot/chats/{chat_id}", headers=owner, json={"all_documents": False})
    assert r.status_code == 200 and r.json()["all_documents"] is False
    chats = (await api.client.get("/v1/bot", headers=owner)).json()["bot"]["chats"]
    assert chats[0]["id"] == str(chat_id) and chats[0]["all_documents"] is False
    assert (await api.client.delete(f"/v1/bot/chats/{chat_id}", headers=owner)).status_code == 204
    assert (await api.client.get("/v1/bot", headers=owner)).json()["bot"]["chats"] == []


async def test_foreign_chat_404(api, make_tenant, owner, tg):
    await connect(api, owner)
    chat_id = await add_chat(api)
    other, _ = await session_for(api, await make_tenant("Вторая"))
    for method, payload in (("PATCH", {"all_documents": False}), ("DELETE", None)):
        r = await api.client.request(method, f"/v1/bot/chats/{chat_id}", headers=other, json=payload)
        assert r.status_code == 404
    bad = await api.client.delete("/v1/bot/chats/not-a-uuid", headers=owner)
    assert bad.status_code == 404
    async with api.sessionmaker() as s:
        assert len((await s.execute(select(BotChat))).scalars().all()) == 1


async def test_patch_default_schema(api, owner, tg):
    await connect(api, owner)
    r = await api.client.patch("/v1/bot", headers=owner, json={"default_schema": "invoice_ru"})
    assert r.status_code == 200 and r.json()["default_schema"] == "invoice_ru"
    r = await api.client.patch("/v1/bot", headers=owner, json={"default_schema": "auto"})
    assert r.status_code == 200 and r.json()["default_schema"] is None
    await api.client.patch("/v1/bot", headers=owner, json={"default_schema": "invoice_ru"})
    r = await api.client.patch("/v1/bot", headers=owner, json={"default_schema": None})
    assert r.json()["default_schema"] is None
    r = await api.client.patch("/v1/bot", headers=owner, json={"default_schema": "nope"})
    assert r.status_code == 422


async def test_patch_without_bot_404(api, owner, tg):
    r = await api.client.patch("/v1/bot", headers=owner, json={"default_schema": None})
    assert r.status_code == 404
