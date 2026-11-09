import pytest

from catalogbot.cli import main


def test_runs_on_directory(tmp_path, capsys):
    assert main([str(tmp_path)]) == 0
    assert "CatalogBot" in capsys.readouterr().out


def test_rejects_missing_directory(tmp_path):
    with pytest.raises(SystemExit):
        main([str(tmp_path / "nope")])
