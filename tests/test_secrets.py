import pytest
from cryptography.fernet import Fernet

from docflow.secrets import SecretsKeyMissing, decrypt_secrets, encrypt_secrets


def test_roundtrip():
    key = Fernet.generate_key().decode()
    token = encrypt_secrets({"token": "абв", "n": 1}, key)
    assert "абв" not in token
    assert decrypt_secrets(token, key) == {"token": "абв", "n": 1}


@pytest.mark.parametrize("key", ["", "not-a-fernet-key"])
def test_missing_or_bad_key(key):
    with pytest.raises(SecretsKeyMissing):
        encrypt_secrets({"a": "b"}, key)
    with pytest.raises(SecretsKeyMissing):
        decrypt_secrets("x", key)


def test_wrong_key_cannot_decrypt():
    token = encrypt_secrets({"a": "b"}, Fernet.generate_key().decode())
    with pytest.raises(SecretsKeyMissing):
        decrypt_secrets(token, Fernet.generate_key().decode())
