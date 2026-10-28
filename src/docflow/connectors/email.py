"""Почта: уведомления через SMTP (stdlib smtplib в потоке).

Тема собирается из текста документа (OCR), поэтому всё, что попадает в заголовки, очищается от CR/LF.
Сервер задаётся именем хоста, а не URL: перед сохранением и перед отправкой хост проверяется на публичность (SSRF).
"""

import asyncio
import re
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

from docflow.config import get_settings
from docflow.connectors.base import REGISTRY, BaseConnector, ConnectorError, DestCtx, ExportDoc, FieldDef
from docflow.connectors.notify_common import PURPOSES, doc_ref, notification_text
from docflow.webhooks import check_host

TIMEOUT = 20.0
MAX_RECIPIENTS = 20
SECURITY = ("starttls", "ssl", "none")
_ADDR = re.compile(r"[^\s@<>,;\"']+@[^\s@<>,;\"']+\.[^\s@<>,;\"']+")
_HOST = re.compile(r"[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?|\[[0-9A-Fa-f:.]+\]")
SUBJECTS = {"notify_review": "DocFlow: нужна проверка — ", "notify_new": "DocFlow: новый документ — "}
NOT_SUPPORTED = "сервер не поддерживает нужную функцию (STARTTLS/AUTH/SMTPUTF8)"
SIGN_OFF = "\n\n--\nDocFlow. Это автоматическое уведомление."


def _addr(value: str, what: str) -> str:
    value = value.strip()
    if not value.isascii():
        raise ConnectorError(f"{what}: адрес «{value[:80]}» должен быть из латинских символов (без SMTPUTF8)")
    if not _ADDR.fullmatch(value):
        raise ConnectorError(f"{what}: «{value[:80]}» не похоже на адрес почты")
    return value


def _recipients(config: dict[str, Any]) -> list[str]:
    raw = str(config.get("to_addrs") or "")
    items = [x for x in re.split(r"[,;]", raw) if x.strip()]
    if not items:
        raise ConnectorError("не указаны получатели письма")
    if len(items) > MAX_RECIPIENTS:
        raise ConnectorError(f"получателей не больше {MAX_RECIPIENTS}")
    return [_addr(x, "получатель") for x in items]


def _server(config: dict[str, Any]) -> tuple[str, int, str]:
    host = str(config.get("host") or "").strip()
    if not host or not _HOST.fullmatch(host):
        raise ConnectorError("укажите имя почтового сервера (хост, без http:// и порта)")
    raw_port = config.get("port")
    try:
        port = int(str(raw_port).strip()) if raw_port not in (None, "") else 587
    except ValueError:
        raise ConnectorError("порт должен быть числом") from None
    if not 1 <= port <= 65535:
        raise ConnectorError("порт должен быть от 1 до 65535")
    security = str(config.get("security") or "starttls").strip()
    if security not in SECURITY:
        raise ConnectorError("защита соединения: starttls, ssl или none")
    return host, port, security


def _validate(config: dict[str, Any]) -> tuple[str, list[str]]:
    """Форма настроек целиком. Возвращает отправителя и получателей."""
    _server(config)
    return _addr(str(config.get("from_addr") or ""), "отправитель"), _recipients(config)


def _close(client: smtplib.SMTP | None) -> None:
    if client is None:
        return
    try:
        client.quit()
    except Exception:
        try:
            client.close()
        except Exception:
            pass


def _connect(config: dict[str, Any], secrets: dict[str, Any]) -> smtplib.SMTP:
    """Соединение, шифрование и вход. Ошибки приведены к ConnectorError (без текста сервера: там бывают данные)."""
    host, port, security = _server(config)
    username = str(secrets.get("username") or "")
    password = str(secrets.get("password") or "")
    if password and not username:
        raise ConnectorError("укажите логин или уберите пароль")
    if username and security == "none" and not get_settings().webhook_allow_private:
        raise ConnectorError("вход без шифрования запрещён — выберите starttls или ssl")
    client: smtplib.SMTP | None = None
    try:
        if security == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            client = smtplib.SMTP(host, port, timeout=TIMEOUT)
            if security == "starttls":
                client.starttls(context=ssl.create_default_context())
        if username:
            client.login(username, password)
        return client
    except smtplib.SMTPAuthenticationError:
        _close(client)
        raise ConnectorError("сервер не принял логин или пароль") from None
    except smtplib.SMTPNotSupportedError:
        _close(client)
        raise ConnectorError(NOT_SUPPORTED) from None
    except smtplib.SMTPResponseException as exc:
        _close(client)
        raise ConnectorError(
            f"почтовый сервер отклонил соединение (код {exc.smtp_code})", retryable=400 <= exc.smtp_code < 500
        ) from None
    except ssl.SSLError:
        _close(client)
        raise ConnectorError("не удалось установить защищённое соединение — проверьте порт и режим защиты") from None
    except (OSError, smtplib.SMTPException) as exc:
        _close(client)
        raise ConnectorError(f"почтовый сервер недоступен ({type(exc).__name__})", retryable=True) from None


def _message(doc: ExportDoc, purpose: str, sender: str, recipients: list[str]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = (SUBJECTS[purpose] + doc_ref(doc))[:200]  # doc_ref уже в одну строку: без CR/LF
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(notification_text(doc, purpose) + SIGN_OFF, charset="utf-8")
    return msg


def _refused(names: Any) -> ConnectorError:
    return ConnectorError(f"сервер отклонил получателей: {', '.join(sorted(str(a) for a in names)[:5])}")


def _send(config: dict[str, Any], secrets: dict[str, Any], msg: EmailMessage, sender: str, to: list[str]) -> None:
    client = _connect(config, secrets)
    try:
        refused = client.send_message(msg, from_addr=sender, to_addrs=to)
        if refused:
            raise _refused(refused)
    except ConnectorError:
        raise
    except smtplib.SMTPRecipientsRefused as exc:
        raise _refused(exc.recipients) from None
    except smtplib.SMTPNotSupportedError:
        raise ConnectorError(NOT_SUPPORTED) from None
    except smtplib.SMTPSenderRefused as exc:
        raise ConnectorError(f"сервер не принял отправителя {sender}", retryable=400 <= exc.smtp_code < 500) from None
    except smtplib.SMTPResponseException as exc:
        raise ConnectorError(
            f"почтовый сервер отклонил письмо (код {exc.smtp_code})", retryable=400 <= exc.smtp_code < 500
        ) from None
    except (OSError, smtplib.SMTPException) as exc:
        raise ConnectorError(f"не удалось отправить письмо ({type(exc).__name__})", retryable=True) from None
    finally:
        _close(client)


class Email(BaseConnector):
    kind = "email"
    label = "Почта"
    config_fields = [
        FieldDef(
            "host", "SMTP-сервер", required=True, help="Имя хоста, например smtp.yandex.ru (без http:// и порта)."
        ),
        FieldDef("port", "Порт", type="number", help="Пусто — 587 (STARTTLS); для SSL обычно 465."),
        FieldDef(
            "security",
            "Защита соединения",
            type="select",
            options=list(SECURITY),
            help="starttls — по умолчанию; ssl — порт 465; none — без шифрования.",
        ),
        FieldDef("from_addr", "Адрес отправителя", required=True),
        FieldDef("to_addrs", "Получатели", required=True, help=f"Через запятую, не больше {MAX_RECIPIENTS}."),
    ]
    secret_fields = [
        FieldDef("username", "Логин", help="Пусто, если сервер не требует входа."),
        FieldDef("password", "Пароль", help="Для Яндекса/Gmail — пароль приложения."),
    ]

    def check_network(self, config: dict[str, Any], *, allow_private: bool = False) -> None:
        """Сохранение и отправка: форма адресов и публичность хоста. ConnectorError/ValueError при нарушении."""
        _validate(config)  # ConnectorError с понятным текстом
        host, port, _ = _server(config)
        check_host(host.strip("[]"), port, allow_private=allow_private)

    async def test(self, ctx: DestCtx) -> str | None:
        _validate(ctx.config)
        client = await asyncio.to_thread(_connect, ctx.config, ctx.secrets)
        await asyncio.to_thread(_close, client)
        return "Почтовый сервер принял вход"

    async def notify(self, ctx: DestCtx, doc: ExportDoc, purpose: str) -> None:
        if purpose not in PURPOSES:
            raise ConnectorError(f"«{self.label}» не умеет это назначение: {purpose}")
        sender, recipients = _validate(ctx.config)
        msg = _message(doc, purpose, sender, recipients)
        await asyncio.to_thread(_send, ctx.config, ctx.secrets, msg, sender, recipients)


REGISTRY["email"] = Email()
