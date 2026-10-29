"""Шифрование секретов получателей (ключи, токены, пароли клиента): Fernet, ключ DOCFLOW_SECRETS_KEY."""

import json

from cryptography.fernet import Fernet, InvalidToken

NO_KEY = "на сервере не настроен ключ шифрования (DOCFLOW_SECRETS_KEY)"


class SecretsKeyMissing(Exception):
    """Ключ шифрования не задан, некорректен или не подходит к сохранённым данным."""


def _fernet(key: str) -> Fernet:
    if not key:
        raise SecretsKeyMissing(NO_KEY)
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise SecretsKeyMissing("ключ шифрования DOCFLOW_SECRETS_KEY некорректен") from None


def encrypt_secrets(d: dict, key: str) -> str:
    return _fernet(key).encrypt(json.dumps(d, ensure_ascii=False).encode()).decode()


def decrypt_secrets(token: str, key: str) -> dict:
    try:
        return json.loads(_fernet(key).decrypt(token.encode()))
    except InvalidToken:
        raise SecretsKeyMissing("секреты получателя не расшифровываются: ключ шифрования изменился") from None
