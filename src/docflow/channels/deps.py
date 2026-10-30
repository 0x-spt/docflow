"""Зависимости канальных процессов."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import async_sessionmaker

from docflow.config import Settings
from docflow.intake import IntakeDeps


@dataclass
class ChannelDeps:
    sessionmaker: async_sessionmaker
    intake: IntakeDeps
    settings: Settings
