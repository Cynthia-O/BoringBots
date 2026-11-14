import os
from datetime import datetime

import pytest


@pytest.fixture(autouse=True)
def isolate(monkeypatch, tmp_path):
    """Keep tests away from the real ExpiryBot folder, trace file and browser."""
    monkeypatch.setenv("EXPIRYBOT_HOME", str(tmp_path / "expirybot-home"))
    monkeypatch.setenv("BORINGBOTS_TRACES", str(tmp_path / "traces.sqlite"))
    monkeypatch.setattr("expirybot.cli.open_in_browser", lambda path: True)


@pytest.fixture
def make_file():
    """make_file(root, relpath, modified="YYYY-MM-DD"): create a file below root
    with that modified date."""

    def make(root, relpath, modified="2020-06-01", text="x"):
        path = root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        stamp = datetime.fromisoformat(modified).timestamp()
        os.utime(path, (stamp, stamp))
        return path

    return make
