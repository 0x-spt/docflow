from docflow.config import Settings, get_settings


def test_settings_env_prefix(monkeypatch):
    monkeypatch.setenv("DOCFLOW_MAX_PAGES", "5")
    get_settings.cache_clear()
    try:
        assert get_settings().max_pages == 5
    finally:
        get_settings.cache_clear()


def test_defaults():
    assert Settings().anthropic_model == "claude-opus-5-5"


def test_public_base_url_default():
    assert Settings().public_base_url == "http://localhost:8000"


def test_webhook_allow_private_default_off(monkeypatch):
    monkeypatch.delenv("DOCFLOW_WEBHOOK_ALLOW_PRIVATE", raising=False)
    assert Settings(_env_file=None).webhook_allow_private is False
