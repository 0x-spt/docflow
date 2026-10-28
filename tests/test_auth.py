import hashlib

from docflow.auth import generate_session_token, hash_password, verify_password


def test_hash_roundtrip():
    h = hash_password("correct-horse-1")
    assert h.startswith("$argon2id$")
    assert verify_password(h, "correct-horse-1") is True
    assert verify_password(h, "wrong-password") is False


def test_session_token_shape():
    token, digest = generate_session_token()
    assert token.startswith("dfs_")
    assert digest == hashlib.sha256(token.encode()).hexdigest()
    assert generate_session_token()[0] != token
