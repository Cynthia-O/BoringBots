import json

import pytest

from dupebot.cli import main


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


def test_command_line_runs_are_traced(tmp_path):
    from bottrace import read_traces

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    for name in ("Budget Review_v2.docx", "FINAL_Budget Review.docx"):
        (corpus / name).write_bytes(b"x" * 300_000)
    main([str(corpus), "--threshold", "0.6", "--output", str(tmp_path / "r.json")])
    [spans] = read_traces()
    assert spans[-1]["name"] == "command line" or any(s["name"] == "command line" for s in spans)
    assert {"walk folder", "cluster names", "build report"} <= {s["name"] for s in spans}
