"""Уведомления: Telegram (respx) и почта (подменённый smtplib), SSRF хоста, защита заголовков."""

import json
import smtplib
import ssl
import uuid
from datetime import UTC, datetime

import httpx
import pytest
import respx

from docflow.connectors import email as email_mod
from docflow.connectors.base import REGISTRY, ConnectorError, DestCtx, ExportDoc, supports
from docflow.schemas.library import builtin_schemas

TOKEN = "123456:SECRET-token"
API = f"https://api.telegram.org/bot{TOKEN}"


@pytest.fixture
async def http():
    async with httpx.AsyncClient() as client:
        yield client


def make_doc(issues=None, **data) -> ExportDoc:
    schema = builtin_schemas()["invoice_ru"]
    return ExportDoc(
        id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        schema=schema,
        data={"number": "42", "supplier": "ООО Ромашка", "buyer": "ООО Лютик", "total": 12500.5, **data},
        status="ok",
        created_at=datetime(2026, 10, 5, tzinfo=UTC),
        title="Счёт",
        number="42",
        issued=datetime(2026, 10, 5).date(),
        cabinet_url="https://site/app/documents/1",
        issues=issues or [],
    )


def tg_ctx(http, **over) -> DestCtx:
    secrets = {"bot_token": TOKEN}
    return DestCtx(config={"chat_id": "-100500", **over}, secrets=secrets, mapping={}, http=http)


# ---------- Telegram ----------


def test_supports():
    tg, em = REGISTRY["telegram"], REGISTRY["email"]
    for c in (tg, em):
        assert supports(c, "notify_review") and supports(c, "notify_new")
        assert not supports(c, "export")


@respx.mock
async def test_telegram_notify_new_text(http):
    route = respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True, "result": {}}))
    await REGISTRY["telegram"].notify(tg_ctx(http), make_doc(), "notify_new")
    body = json.loads(route.calls[0].request.content)
    assert body["chat_id"] == "-100500"
    assert body["disable_web_page_preview"] is True
    assert "parse_mode" not in body
    assert body["text"] == (
        "Новый документ: Счёт № 42 от 05.10.2026\nООО Ромашка, сумма 12 500,50 ₽\nhttps://site/app/documents/1"
    )


@respx.mock
async def test_telegram_notify_review_reasons(http):
    route = respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
    issues = [{"field": "x", "code": "c", "message": f"причина {i}"} for i in range(5)]
    await REGISTRY["telegram"].notify(tg_ctx(http), make_doc(issues=issues), "notify_review")
    text = json.loads(route.calls[0].request.content)["text"]
    assert text.startswith(
        "Нужна проверка: Счёт № 42 от 05.10.2026\nПричины: причина 0; причина 1; причина 2 и ещё 2\n"
    )
    assert text.endswith("https://site/app/documents/1")


@respx.mock
async def test_telegram_text_capped(http):
    route = respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
    issues = [{"message": "я" * 3000}]
    await REGISTRY["telegram"].notify(tg_ctx(http), make_doc(issues=issues), "notify_review")
    assert len(json.loads(route.calls[0].request.content)["text"]) <= 4000


@respx.mock
async def test_telegram_test_ok(http):
    respx.post(f"{API}/getMe").mock(
        return_value=httpx.Response(200, json={"ok": True, "result": {"username": "dfbot"}})
    )
    respx.post(f"{API}/getChat").mock(return_value=httpx.Response(200, json={"ok": True, "result": {"title": "Бухи"}}))
    msg = await REGISTRY["telegram"].test(tg_ctx(http))
    assert msg == "Бот @dfbot доступен, чат «Бухи» найден"


@respx.mock
@pytest.mark.parametrize(
    ("status", "retryable", "fragment"),
    [
        (401, False, "токен бота не принят"),
        (400, False, "бот не может писать в чат -100500"),
        (403, False, "добавьте бота в чат"),
        (429, True, "ограничил"),
        (502, True, "временно"),
    ],
)
async def test_telegram_errors(http, status, retryable, fragment):
    desc = "Bad Request: chat not found" if status == 400 else "x"
    respx.post(f"{API}/sendMessage").mock(
        return_value=httpx.Response(status, json={"ok": False, "description": desc, "parameters": {"retry_after": 5}})
    )
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["telegram"].notify(tg_ctx(http), make_doc(), "notify_new")
    assert ei.value.retryable is retryable
    assert fragment in ei.value.message
    assert TOKEN not in ei.value.message


@respx.mock
async def test_telegram_timeout_retryable_and_token_hidden(http):
    respx.post(f"{API}/sendMessage").mock(side_effect=httpx.ConnectTimeout("boom " + API))
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["telegram"].notify(tg_ctx(http), make_doc(), "notify_new")
    assert ei.value.retryable
    assert TOKEN not in ei.value.message
    assert ei.value.__cause__ is None  # исключение httpx с URL не тянем за собой


async def test_telegram_export_not_supported(http):
    with pytest.raises(ConnectorError):
        await REGISTRY["telegram"].export(tg_ctx(http), make_doc())


# ---------- Почта ----------


class FakeSMTP:
    instances: list["FakeSMTP"] = []
    fail_login: Exception | None = None
    fail_connect: Exception | None = None
    refuse: dict | None = None

    def __init__(self, host, port, timeout=None, context=None):
        if FakeSMTP.fail_connect:
            raise FakeSMTP.fail_connect
        self.host, self.port, self.timeout, self.context = host, port, timeout, context
        self.calls: list[tuple] = []
        self.sent = None
        FakeSMTP.instances.append(self)

    def starttls(self, context=None):
        self.calls.append(("starttls",))

    def login(self, user, password):
        if FakeSMTP.fail_login:
            raise FakeSMTP.fail_login
        self.calls.append(("login", user, password))

    def send_message(self, msg, from_addr=None, to_addrs=None):
        self.sent = (msg, from_addr, to_addrs)
        return FakeSMTP.refuse or {}

    def quit(self):
        self.calls.append(("quit",))

    def close(self):
        pass


class FakeSMTPSSL(FakeSMTP):
    pass


@pytest.fixture
def smtp(monkeypatch):
    FakeSMTP.instances = []
    FakeSMTP.fail_login = FakeSMTP.fail_connect = FakeSMTP.refuse = None
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    monkeypatch.setattr(smtplib, "SMTP_SSL", FakeSMTPSSL)
    return FakeSMTP


def em_ctx(http, secrets=None, **over) -> DestCtx:
    config = {"host": "smtp.example.com", "from_addr": "df@example.com", "to_addrs": "a@x.ru, b@x.ru", **over}
    return DestCtx(
        config=config, secrets={"username": "u", "password": "p"} if secrets is None else secrets, mapping={}, http=http
    )


async def test_email_notify_starttls(http, smtp):
    issues = [{"message": "ИНН не сходится"}]
    await REGISTRY["email"].notify(em_ctx(http), make_doc(issues=issues), "notify_review")
    c = smtp.instances[0]
    assert (c.host, c.port, c.timeout) == ("smtp.example.com", 587, 20.0)
    assert [x[0] for x in c.calls] == ["starttls", "login", "quit"]
    msg, sender, to = c.sent
    assert (sender, to) == ("df@example.com", ["a@x.ru", "b@x.ru"])
    assert msg["Subject"] == "DocFlow: нужна проверка — Счёт № 42 от 05.10.2026"
    body = msg.get_content()
    assert "Причины: ИНН не сходится" in body and "https://site/app/documents/1" in body
    assert body.rstrip().endswith("автоматическое уведомление.")
    assert msg.get_content_charset() == "utf-8"


async def test_email_ssl_no_auth(http, smtp):
    await REGISTRY["email"].notify(em_ctx(http, secrets={}, security="ssl", port="465"), make_doc(), "notify_new")
    c = smtp.instances[0]
    assert isinstance(c, FakeSMTPSSL) and c.port == 465
    assert [x[0] for x in c.calls] == ["quit"]
    assert c.sent[0]["Subject"].startswith("DocFlow: новый документ — ")


async def test_email_security_none(http, smtp):
    await REGISTRY["email"].notify(em_ctx(http, secrets={}, security="none"), make_doc(), "notify_new")
    assert [x[0] for x in smtp.instances[0].calls] == ["quit"]


async def test_email_header_injection(http, smtp):
    doc = make_doc()
    doc.title = "Счёт\r\nBcc: evil@x.ru"
    doc.number = "1\nSubject: x"
    await REGISTRY["email"].notify(em_ctx(http), doc, "notify_new")
    msg = smtp.instances[0].sent[0]
    assert "\n" not in str(msg["Subject"]) and "\r" not in str(msg["Subject"])
    assert msg["Bcc"] is None
    assert [k for k, _ in msg.items()].count("Subject") == 1


async def test_email_test_ok_and_auth_error(http, smtp):
    assert await REGISTRY["email"].test(em_ctx(http)) == "Почтовый сервер принял вход"
    smtp.fail_login = smtplib.SMTPAuthenticationError(535, b"bad creds secret")
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].test(em_ctx(http))
    assert not ei.value.retryable and ei.value.message == "сервер не принял логин или пароль"


async def test_email_retryable_connection_errors(http, smtp):
    for exc in (ConnectionRefusedError(), TimeoutError(), smtplib.SMTPServerDisconnected("x"), OSError("dns")):
        smtp.fail_connect = exc
        with pytest.raises(ConnectorError) as ei:
            await REGISTRY["email"].notify(em_ctx(http), make_doc(), "notify_new")
        assert ei.value.retryable


async def test_email_tls_error_not_retryable(http, smtp):
    smtp.fail_connect = ssl.SSLError("wrong version")
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http), make_doc(), "notify_new")
    assert not ei.value.retryable


async def test_email_recipients_refused(http, smtp):
    smtp.refuse = {"b@x.ru": (550, b"no such user")}
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http), make_doc(), "notify_new")
    assert not ei.value.retryable and "b@x.ru" in ei.value.message


@pytest.mark.parametrize(
    "over",
    [
        {"to_addrs": ""},
        {"to_addrs": "not-an-address"},
        {"to_addrs": ",".join(f"u{i}@x.ru" for i in range(21))},
        {"from_addr": "a@b.ru\r\nBcc: x@y.ru"},
        {"host": "http://smtp.example.com"},
        {"host": "smtp.example.com:25"},
        {"port": "abc"},
        {"port": "70000"},
        {"security": "tls13"},
    ],
)
async def test_email_config_validation(http, smtp, over):
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http, **over), make_doc(), "notify_new")
    assert not ei.value.retryable
    assert not smtp.instances


def test_email_check_network_ssrf():
    em = REGISTRY["email"]
    cfg = {"host": "127.0.0.1", "from_addr": "df@example.com", "to_addrs": "a@x.ru"}
    with pytest.raises(ValueError, match="не публичный"):
        em.check_network(cfg)
    em.check_network(cfg, allow_private=True)
    with pytest.raises(ConnectorError):
        em.check_network({**cfg, "to_addrs": "bad"}, allow_private=True)


def test_email_module_does_not_shadow_stdlib():
    import email.message

    assert email.message.EmailMessage is email_mod.EmailMessage


# ---------- Ревью: дополнения ----------


@respx.mock
async def test_review_reasons_skip_info_codes(http):
    route = respx.post(f"{API}/sendMessage").mock(return_value=httpx.Response(200, json={"ok": True}))
    issues = [
        {"code": "corrected", "message": "поправили сумму"},
        {"code": "inn_checksum", "message": "ИНН не сходится"},
        {"code": "corrected", "message": "поправили дату"},
    ]
    await REGISTRY["telegram"].notify(tg_ctx(http), make_doc(issues=issues), "notify_review")
    text = json.loads(route.calls[0].request.content)["text"]
    assert "Причины: ИНН не сходится\n" in text
    assert "поправили" not in text and "ещё" not in text


async def test_email_plain_auth_refused(http, smtp):
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http, security="none"), make_doc(), "notify_new")
    assert not ei.value.retryable
    assert ei.value.message == "вход без шифрования запрещён — выберите starttls или ssl"
    assert not smtp.instances


async def test_email_plain_auth_allowed_in_dev(http, smtp, monkeypatch):
    from docflow import config

    monkeypatch.setattr(email_mod, "get_settings", lambda: config.Settings(webhook_allow_private=True))
    await REGISTRY["email"].notify(em_ctx(http, security="none"), make_doc(), "notify_new")
    assert smtp.instances[0].sent


async def test_email_not_supported_is_permanent(http, smtp):
    smtp.fail_login = smtplib.SMTPNotSupportedError("STARTTLS extension not supported by server.")
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http), make_doc(), "notify_new")
    assert not ei.value.retryable
    assert ei.value.message == "сервер не поддерживает нужную функцию (STARTTLS/AUTH/SMTPUTF8)"


async def test_email_sender_refused_4xx_retryable(http, smtp, monkeypatch):
    def boom(self, *a, **k):
        raise smtplib.SMTPSenderRefused(452, b"try later", "df@example.com")

    monkeypatch.setattr(smtp, "send_message", boom)
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http), make_doc(), "notify_new")
    assert ei.value.retryable

    def hard(self, *a, **k):
        raise smtplib.SMTPSenderRefused(553, b"no", "df@example.com")

    monkeypatch.setattr(smtp, "send_message", hard)
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http), make_doc(), "notify_new")
    assert not ei.value.retryable


async def test_email_non_ascii_address_rejected(http, smtp):
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http, to_addrs="почта@пример.рф"), make_doc(), "notify_new")
    assert not ei.value.retryable and not smtp.instances


async def test_email_password_without_username(http, smtp):
    with pytest.raises(ConnectorError) as ei:
        await REGISTRY["email"].notify(em_ctx(http, secrets={"password": "p"}), make_doc(), "notify_new")
    assert ei.value.message == "укажите логин или уберите пароль" and not ei.value.retryable


def test_email_check_network_form_error_is_connector_error():
    cfg = {"host": "smtp.example.com", "from_addr": "df@example.com", "to_addrs": "bad"}
    with pytest.raises(ConnectorError):
        REGISTRY["email"].check_network(cfg, allow_private=True)


def test_httpx_logger_quiet_after_startup():
    import logging

    from docflow.api.app import create_app
    from docflow.config import Settings

    logging.getLogger("httpx").setLevel(logging.INFO)
    create_app(Settings())
    assert logging.getLogger("httpx").level == logging.WARNING


def labeled_doc(labels, issues=None):
    from docflow.labels import apply_labels

    doc = make_doc(issues=issues)
    doc.base_schema = doc.schema
    doc.schema = apply_labels(doc.schema, labels)
    return doc


def test_notify_new_uses_total_label():
    from docflow.connectors.notify_common import notification_text

    text = notification_text(labeled_doc({"total": "К оплате"}), "notify_new")
    assert text.splitlines()[1] == "ООО Ромашка, К оплате: 12 500,50 ₽"


def test_notify_review_reason_prefixed_by_label():
    from docflow.connectors.notify_common import notification_text

    issues = [{"field": "vat", "code": "c", "message": "не сходится"}]
    assert "Причины: Налог: не сходится" in notification_text(labeled_doc({"vat": "Налог"}, issues), "notify_review")
    plain = notification_text(labeled_doc({}, issues), "notify_review")
    assert "Причины: не сходится" in plain and "Налог" not in plain


def test_notify_new_without_total_does_not_crash():
    """Тип без суммы (путевой лист из конструктора): уведомление идёт без «суммы», а не падает TypeError."""
    from docflow.connectors.notify_common import notification_text

    text = notification_text(make_doc(total=None), "notify_new")
    assert text.splitlines()[1] == "ООО Ромашка"
    assert "₽" not in text
