"""Настройки сервиса. Всё читается из окружения с префиксом DOCFLOW_ (или из .env)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DOCFLOW_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://docflow:docflow@localhost:5432/docflow"
    redis_url: str = "redis://localhost:6379/0"

    # local — для разработки и тестов без Docker, s3 — MinIO/S3 в проде
    blob_backend: Literal["local", "s3"] = "local"
    blob_root: Path = Path("data/blobs")
    s3_endpoint: str | None = None
    s3_bucket: str = "docflow"
    s3_access_key: str | None = None
    s3_secret_key: str | None = None

    # ключ Anthropic берёт сам SDK из ANTHROPIC_API_KEY, здесь его не храним
    anthropic_model: str = "claude-opus-5-5"
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5vl:7b"

    # внешний адрес API: попадает в pdf_url в ответах и вебхуках
    public_base_url: str = "http://localhost:8000"

    # вебхуки на частные/loopback адреса: только для dev и e2e
    webhook_allow_private: bool = False

    # лимиты на входе: проверяем при загрузке, чтобы воркер не получал мусор
    max_file_mb: int = 20
    max_pages: int = 30


@lru_cache
def get_settings() -> Settings:
    # кэш, чтобы не перечитывать окружение на каждый запрос; в тестах сбрасывается cache_clear()
    return Settings()
