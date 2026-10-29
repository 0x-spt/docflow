"""Проверка настроек и секретов подключения по полям коннектора: общая для API и команды seed-demo."""

import asyncio
from typing import Any

from docflow.connectors.base import Connector, ConnectorError, FieldDef
from docflow.webhooks import check_webhook_url


class InvalidSettings(ValueError):
    """Настройки или секреты не подходят коннектору; текст — для человека."""


def check_names(given: dict[str, Any], fields: list[FieldDef], what: str) -> None:
    known = {f.name for f in fields}
    unknown = sorted(set(given) - known)
    if unknown:
        raise InvalidSettings(f"{what}: неизвестные поля {', '.join(unknown)}")


def check_required(given: dict[str, Any], fields: list[FieldDef], what: str) -> None:
    for f in fields:
        if f.required and given.get(f.name) in (None, ""):
            raise InvalidSettings(f"{what}: заполните «{f.label}»")


async def check_urls(values: dict[str, Any], fields: list[FieldDef], *, allow_private: bool) -> None:
    """SSRF-проверка полей типа url (и в настройках, и в секретах: адрес вебхука Битрикс24 — секрет)."""
    for f in fields:
        value = values.get(f.name)
        if f.type != "url" or value in (None, ""):
            continue
        try:
            if not isinstance(value, str):
                raise ValueError("адрес должен быть строкой")
            # getaddrinfo блокирующий: в отдельном потоке, чтобы не держать цикл событий
            await asyncio.to_thread(check_webhook_url, value, allow_private=allow_private)
        except ValueError as exc:
            raise InvalidSettings(f"«{f.label}»: недопустимый адрес: {exc}") from None


async def check_config(config: dict[str, Any], connector: Connector, *, allow_private: bool) -> None:
    check_names(config, connector.config_fields, "настройки")
    check_required(config, connector.config_fields, "настройки")
    await check_urls(config, connector.config_fields, allow_private=allow_private)
    # коннекторам с хостом вместо URL (почта): своя сетевая проверка
    check_network = getattr(connector, "check_network", None)
    if check_network is not None:
        try:
            await asyncio.to_thread(check_network, config, allow_private=allow_private)
        except ConnectorError as exc:
            raise InvalidSettings(f"настройки: {exc.message}") from None
        except ValueError as exc:
            raise InvalidSettings(f"недопустимый адрес сервера: {exc}") from None


async def check_secrets(secrets: dict[str, Any], connector: Connector, *, allow_private: bool) -> None:
    check_names(secrets, connector.secret_fields, "секреты")
    check_required(secrets, connector.secret_fields, "секреты")
    await check_urls(secrets, connector.secret_fields, allow_private=allow_private)
