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
