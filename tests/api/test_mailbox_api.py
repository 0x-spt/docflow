import logging

import pytest
from api_helpers import session_for
from sqlalchemy import select

from docflow.channels.mailbox import AUTH_ERROR, NETWORK_ERROR, MailboxAuthError, MailboxCreds, open_imap
from docflow.db.models import Mailbox
from docflow.secrets import decrypt_secrets

PASSWORD = "synthetic-mail-pass-1"
HOST = "8.8.8.8"  # числовой адрес: проверка хоста обходится без сети


class FakeSession:
    def __init__(self, unseen=3):
        self.unseen = unseen
        self.closed = 0

    def unseen_uids(self):
        return [str(n) for n in range(self.unseen)]

    def close(self):
        self.closed += 1


class FakeConnect:
    def __init__(self, error: Exception | None = None):
        self.error = error
        self.session = FakeSession()
        self.creds: list[MailboxCreds] = []

    def __call__(self, creds):
        self.creds.append(creds)
        if self.error is not None:
            raise self.error
        return self.session


@pytest.fixture
async def owner(api, auth):
    headers, _ = await session_for(api, auth)
    return headers


@pytest.fixture
def imap(api):
    fake = FakeConnect()
    api.app.state.imap_connect = fake
    return fake


def body(**over) -> dict:
    return {"host": HOST, "username": "in@acme.example", "password": PASSWORD, **over}


async def put(api, headers, **over):
    return await api.client.put("/v1/mailbox", headers=headers, json=body(**over))


async def row(api) -> Mailbox:
    async with api.sessionmaker() as s:
        return (await s.execute(select(Mailbox))).scalars().one()


async def test_put_get_never_returns_password(api, owner, caplog):
    caplog.set_level(logging.DEBUG)
    assert (await api.client.get("/v1/mailbox", headers=owner)).json() == {"mailbox": None}
    r = await put(api, owner, allowed_senders=["Boss@Firma.example", "@FIRMA.example"], default_schema="invoice_ru")
    assert r.status_code == 200, r.text
    saved = await row(api)
    assert decrypt_secrets(saved.password_enc, api.settings.secrets_key) == {"password": PASSWORD}
    assert PASSWORD not in saved.password_enc
    got = await api.client.get("/v1/mailbox", headers=owner)
    for response in (r, got):
        assert PASSWORD not in response.text and saved.password_enc not in response.text
        assert "password" not in response.text
    assert got.json() == {
        "mailbox": {
            "host": HOST,
            "port": 993,
            "username": "in@acme.example",
            "folder": "INBOX",
            "allowed_senders": ["boss@firma.example", "@firma.example"],
            "default_schema": "invoice_ru",
            "active": True,
            "last_error": None,
            "last_checked_at": None,
        }
    }
    assert r.json() == got.json()["mailbox"]
    assert PASSWORD not in caplog.text


async def test_put_without_password_on_create_422(api, owner):
    for over in ({"password": ""}, {"password": None}):
        r = await put(api, owner, **over)
        assert r.status_code == 422, over
    payload = body()
    del payload["password"]
    r = await api.client.put("/v1/mailbox", headers=owner, json=payload)
    assert r.status_code == 422
    async with api.sessionmaker() as s:
        assert (await s.execute(select(Mailbox))).first() is None


async def test_put_keeps_password_when_empty(api, owner):
    await put(api, owner)
    first = (await row(api)).password_enc
    for over in ({"password": ""}, {"password": None}):
        r = await put(api, owner, folder="Bills", **over)
        assert r.status_code == 200, r.text
        assert (await row(api)).password_enc == first
    assert (await row(api)).folder == "Bills"
    await put(api, owner, password="synthetic-new-pass")
    saved = (await row(api)).password_enc
    assert decrypt_secrets(saved, api.settings.secrets_key) == {"password": "synthetic-new-pass"}


async def test_put_empty_password_with_new_address_422(api, owner):
    await put(api, owner)
    first = (await row(api)).password_enc
    for over in ({"host": "1.1.1.1"}, {"port": 1143}):
        r = await put(api, owner, password="", **over)
        assert r.status_code == 422, over
        assert r.json()["detail"] == "Укажите пароль: адрес сервера изменился"
    box = await row(api)
    assert (box.host, box.port, box.password_enc) == (HOST, 993, first)
    r = await put(api, owner, host="1.1.1.1", password="synthetic-new-pass")
    assert r.status_code == 200, r.text


async def test_put_resets_error_and_active(api, owner):
    await put(api, owner)
    async with api.sessionmaker() as s:
        box = (await s.execute(select(Mailbox))).scalars().one()
        box.active, box.last_error = False, AUTH_ERROR
        await s.commit()
    r = await put(api, owner, password="")
    assert r.json()["active"] is True and r.json()["last_error"] is None
    assert (await row(api)).active is True


async def test_delete(api, owner):
    await put(api, owner)
    r = await api.client.delete("/v1/mailbox", headers=owner)
    assert r.status_code == 204
    assert (await api.client.get("/v1/mailbox", headers=owner)).json() == {"mailbox": None}
    assert (await api.client.delete("/v1/mailbox", headers=owner)).status_code == 404


async def test_test_reports_unseen(api, owner, imap):
    await put(api, owner, port=1143, folder="Bills")
    r = await api.client.post("/v1/mailbox/test", headers=owner)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ok"] is True and out["unseen"] == 3 and "3" in out["message"]
    assert imap.session.closed == 1
    creds = imap.creds[0]
    assert (creds.host, creds.port, creds.username, creds.folder) == (HOST, 1143, "in@acme.example", "Bills")
    assert creds.password == PASSWORD


async def test_test_failure_has_no_password(api, owner, imap, caplog):
    caplog.set_level(logging.DEBUG)
    await put(api, owner)
    for error, expected in (
        (RuntimeError(f"LOGIN {PASSWORD}"), NETWORK_ERROR),
        (MailboxAuthError(AUTH_ERROR), AUTH_ERROR),
    ):
        imap.error = error
        r = await api.client.post("/v1/mailbox/test", headers=owner)
        assert r.status_code == 200
        assert r.json() == {"ok": False, "message": expected, "unseen": None}
        assert PASSWORD not in r.text
    assert PASSWORD not in caplog.text


async def test_test_does_not_change_state(api, owner, imap):
    await put(api, owner)
    imap.error = MailboxAuthError(AUTH_ERROR)
    await api.client.post("/v1/mailbox/test", headers=owner)
    box = await row(api)
    assert box.active is True and box.last_error is None


async def test_test_without_mailbox_404(api, owner, imap):
    assert (await api.client.post("/v1/mailbox/test", headers=owner)).status_code == 404


async def test_test_rechecks_host(api, owner, imap):
    await put(api, owner)
    async with api.sessionmaker() as s:
        box = (await s.execute(select(Mailbox))).scalars().one()
        box.host = "10.0.0.1"
        await s.commit()
    r = await api.client.post("/v1/mailbox/test", headers=owner)
    assert r.json()["ok"] is False and imap.creds == []
    assert "10.0.0.1" not in r.text


async def test_test_uses_open_imap_by_default(api):
    assert api.app.state.imap_connect is open_imap


async def test_private_host_422(api, owner):
    for host in ("10.0.0.1", "127.0.0.1", "169.254.169.254"):
        r = await put(api, owner, host=host)
        assert r.status_code == 422, host
    api.settings.webhook_allow_private = True
    r = await put(api, owner, host="10.0.0.1")
    assert r.status_code == 200, r.text


async def test_unresolvable_host_422(api, owner, monkeypatch):
    def no_dns(*args, **kwargs):
        raise OSError("dns down")

    monkeypatch.setattr("socket.getaddrinfo", no_dns)
    r = await put(api, owner, host="imap.example.test")
    assert r.status_code == 422 and "dns down" not in r.text


@pytest.mark.parametrize(
    "host",
    ["", "imap example.test", "imap.example.test/", "user@imap.example.test", "imap\n.example.test", "а.рф", "a" * 254],
)
async def test_bad_host_422(api, owner, host):
    assert (await put(api, owner, host=host)).status_code == 422


@pytest.mark.parametrize("port", [0, -1, 65536])
async def test_bad_port_422(api, owner, port):
    assert (await put(api, owner, port=port)).status_code == 422


async def test_bad_folder_and_senders_422(api, owner):
    for folder in ('IN"BOX', "IN\\BOX", "IN\rBOX", "IN\nBOX", "\x00", "\x7f", "", "Счета"):
        assert (await put(api, owner, folder=folder)).status_code == 422, repr(folder)
    for senders in (
        ["firma.example"],
        ["a@"],
        ["@"],
        ["a@b@c.example"],
        ["a b@c.example"],
        ["@c"],
        ["@.c"],
        [""],
        ["a\x00b@c.example"],
        ["@c\x01.example"],
    ):
        assert (await put(api, owner, allowed_senders=senders)).status_code == 422, senders
    async with api.sessionmaker() as s:
        assert (await s.execute(select(Mailbox))).first() is None


async def test_command_injection_refused(api, owner):
    for over in ({"username": "a\r\nb"}, {"password": "a\r\nb"}, {"password": "a\x00b"}, {"username": ""}):
        r = await put(api, owner, **over)
        assert r.status_code == 422, over
    async with api.sessionmaker() as s:
        assert (await s.execute(select(Mailbox))).first() is None


async def test_unknown_default_schema_422_auto_means_none(api, owner):
    assert (await put(api, owner, default_schema="nope")).status_code == 422
    r = await put(api, owner, default_schema="auto")
    assert r.status_code == 200 and r.json()["default_schema"] is None


async def test_no_secrets_key_422(api, owner):
    api.settings.secrets_key = ""
    r = await put(api, owner)
    assert r.status_code == 422 and "DOCFLOW_SECRETS_KEY" in r.json()["detail"]


async def test_test_without_secrets_key_is_failure(api, owner, imap):
    await put(api, owner)
    api.settings.secrets_key = ""
    r = await api.client.post("/v1/mailbox/test", headers=owner)
    assert r.status_code == 200 and r.json()["ok"] is False
    assert imap.creds == []


async def test_422_does_not_echo_password(api, owner, imap):
    bodies = [
        {**body(), "extra": PASSWORD},  # лишнее поле
        {**body(), "password": [PASSWORD]},  # пароль не строкой
        {**body(), "password": {PASSWORD: PASSWORD}},
        {**body(), PASSWORD: "x"},  # пароль в имени ключа
        {**body(), "port": PASSWORD},  # значение не того поля
        {**body(), "host": 5, "other": PASSWORD},
        {**body(), "allowed_senders": [PASSWORD]},  # ручная проверка
        {**body(), "allowed_senders": [{PASSWORD: 1}]},
        {**body(), "folder": f'"{PASSWORD}'},
        {**body(), "host": f"{PASSWORD}!"},
        [PASSWORD],  # тело не объект
        PASSWORD,
    ]
    for payload in bodies:
        r = await api.client.put("/v1/mailbox", headers=owner, json=payload)
        assert r.status_code == 422, payload
        assert PASSWORD not in r.text, payload
    r = await api.client.put(
        "/v1/mailbox",
        headers={**owner, "content-type": "application/json"},
        content=f'{{"password": "{PASSWORD}", '.encode(),
    )
    assert r.status_code == 422 and PASSWORD not in r.text


async def test_operator_and_api_key_403(api, auth, imap):
    operator, _ = await session_for(api, auth, role="operator")
    for headers in (operator, auth):
        for method, url, payload in (
            ("GET", "/v1/mailbox", None),
            ("PUT", "/v1/mailbox", body()),
            ("DELETE", "/v1/mailbox", None),
            ("POST", "/v1/mailbox/test", None),
        ):
            r = await api.client.request(method, url, headers=headers, json=payload)
            assert r.status_code == 403, (method, url, r.status_code)
    assert imap.creds == []


async def test_isolated_between_tenants(api, make_tenant, owner, imap):
    await put(api, owner)
    other, _ = await session_for(api, await make_tenant("Вторая"))
    assert (await api.client.get("/v1/mailbox", headers=other)).json() == {"mailbox": None}
    assert (await api.client.post("/v1/mailbox/test", headers=other)).status_code == 404
    assert (await api.client.delete("/v1/mailbox", headers=other)).status_code == 404
    r = await put(api, other, host="1.1.1.1")
    assert r.status_code == 200
    got = (await api.client.get("/v1/mailbox", headers=owner)).json()["mailbox"]
    assert got["host"] == HOST
    async with api.sessionmaker() as s:
        assert len((await s.execute(select(Mailbox))).scalars().all()) == 2
