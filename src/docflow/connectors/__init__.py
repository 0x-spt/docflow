"""Коннекторы к системам клиента. Реестр REGISTRY наполняют модули коннекторов при импорте."""

from docflow.connectors.base import REGISTRY, ConnectorError, get_connector

__all__ = ["REGISTRY", "ConnectorError", "get_connector"]

# импорт регистрирует коннекторы в REGISTRY
from docflow.connectors import google_sheets  # noqa: E402,F401
