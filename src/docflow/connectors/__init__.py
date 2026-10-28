"""Коннекторы к системам клиента. Реестр REGISTRY наполняют модули коннекторов при импорте."""

from docflow.connectors.base import REGISTRY, ConnectorError, get_connector

__all__ = ["REGISTRY", "ConnectorError", "get_connector"]
