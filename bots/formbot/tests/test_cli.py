import pytest

from formbot.cli import main


def test_accepts_pdf(tmp_path, capsys):
    form = tmp_path / "form.pdf"
    form.write_bytes(b"%PDF-1.4")
    assert main([str(form)]) == 0
    assert "form.pdf" in capsys.readouterr().out


def test_rejects_unsupported_type(tmp_path):
    form = tmp_path / "form.txt"
    form.write_text("hi")
    with pytest.raises(SystemExit):
        main([str(form)])
