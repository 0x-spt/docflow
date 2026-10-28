"""Пароли (argon2id) и токены сессий людей (dfs_…). В БД лежат только хеши."""

import hashlib
import secrets
from datetime import timedelta
from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

SESSION_TTL = timedelta(days=30)
MIN_PASSWORD = 10

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


@lru_cache
def dummy_hash() -> str:
    """Фиксированный хеш для неизвестного email: время ответа не выдаёт, что пользователя нет."""
    return hash_password("docflow-dummy-password")


def generate_session_token() -> tuple[str, str]:
    """(токен, sha256). Сам токен нигде не храним."""
    token = "dfs_" + secrets.token_urlsafe(32)
    return token, hashlib.sha256(token.encode()).hexdigest()
