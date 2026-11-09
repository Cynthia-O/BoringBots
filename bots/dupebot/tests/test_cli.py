import json

import pytest

from dupebot.cli import ask_threshold, main


def answers(*replies):
    it = iter(replies)
    return lambda: next(it)


def test_ask_threshold_uses_default_on_blank():
    assert ask_threshold(answers(""), interactive=True) == 0.6


def test_ask_threshold_reasks_until_valid():
    assert ask_threshold(answers("high", "1.5", "0.75"), interactive=True) == 0.75


def test_ask_threshold_skips_prompt_when_not_interactive():
    assert ask_threshold(answers(), interactive=False) == 0.6


def test_main_writes_report_to_new_file(tmp_path):
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("Budget Review_v2.docx", "FINAL_Budget Review.docx"):
        (corpus / name).write_bytes(b"x" * 300_000)
    out = tmp_path / "report.json"

    assert main([str(corpus), "--threshold", "0.6", "--output", str(out)]) == 0

    report = json.loads(out.read_text())
    assert report["summary"]["clusters"] == 1
    files = report["clusters"][0]["files"]
    assert {f["name"] for f in files} == {"Budget Review_v2.docx", "FINAL_Budget Review.docx"}
    assert all(f["cleaned_name"] == "budget review" for f in files)


def test_main_refuses_to_overwrite(tmp_path):
    out = tmp_path / "report.json"
    out.write_text("keep me")
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--threshold", "0.6", "--output", str(out)])
    assert out.read_text() == "keep me"
