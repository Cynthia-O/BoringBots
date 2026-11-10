import os

from dupebot.env import load_dotenv


def test_loads_nearest_env_from_a_parent_folder(tmp_path, monkeypatch):
    monkeypatch.delenv("DUPEBOT_TEST_KEY", raising=False)
    monkeypatch.delenv("DUPEBOT_TEST_QUOTED", raising=False)
    (tmp_path / ".env").write_text(
        '# a comment\n\nDUPEBOT_TEST_KEY=abc123\nexport DUPEBOT_TEST_QUOTED="x y"\nnot a line\n'
    )
    child = tmp_path / "sub" / "deeper"
    child.mkdir(parents=True)

    assert load_dotenv(child) == tmp_path / ".env"
    assert os.environ["DUPEBOT_TEST_KEY"] == "abc123"
    assert os.environ["DUPEBOT_TEST_QUOTED"] == "x y"


def test_existing_environment_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("DUPEBOT_TEST_KEY", "from-shell")
    (tmp_path / ".env").write_text("DUPEBOT_TEST_KEY=from-file\n")
    load_dotenv(tmp_path)
    assert os.environ["DUPEBOT_TEST_KEY"] == "from-shell"


def test_empty_values_are_not_set(tmp_path, monkeypatch):
    monkeypatch.delenv("DUPEBOT_TEST_EMPTY", raising=False)
    (tmp_path / ".env").write_text("DUPEBOT_TEST_EMPTY=\n")
    load_dotenv(tmp_path)
    assert "DUPEBOT_TEST_EMPTY" not in os.environ
