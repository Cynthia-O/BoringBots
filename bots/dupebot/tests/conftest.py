import pytest


@pytest.fixture(autouse=True)
def isolate_from_real_settings(monkeypatch, tmp_path):
    """Keep tests away from the real call log and the real .env (and its API key)."""
    monkeypatch.setenv("DUPEBOT_DB", str(tmp_path / "calls.sqlite"))
    monkeypatch.setattr("dupebot.cli.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr("dupebot.cli.open_in_browser", lambda path: True)  # no real browser
