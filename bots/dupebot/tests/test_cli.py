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


def test_traces_flag_is_for_the_browser_app(tmp_path):
    with pytest.raises(SystemExit):
        main([str(tmp_path), "--json", "--traces"])


class FakeServer:
    started = []

    def __init__(self, make_app):
        self.make_app = make_app

    def serve(self, open_browser=True):
        FakeServer.started.append((self.make_app(), open_browser))


@pytest.mark.parametrize("extra", [[], ["--ui"]])  # --ui is still accepted
def test_browser_app_is_the_default(tmp_path, monkeypatch, extra):
    monkeypatch.setattr("dupebot.cli.LiveServer", FakeServer)
    FakeServer.started.clear()
    assert main([str(tmp_path), "--no-browser", *extra]) == 0
    [(app, open_browser)] = FakeServer.started
    assert app.__class__.__name__ == "DupeBotApp"
    assert open_browser is False


def test_browser_app_needs_no_folder(monkeypatch):
    monkeypatch.setattr("dupebot.cli.LiveServer", FakeServer)
    FakeServer.started.clear()
    assert main([]) == 0
    assert len(FakeServer.started) == 1


def test_json_prints_the_report(tmp_path, capsys):
    assert main([str(tmp_path), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["summary"]["clusters"] == 0


def test_terminal_modes_need_a_folder():
    for flags in (["--json"], ["--analyze"], ["--output", "r.json"]):
        with pytest.raises(SystemExit):
            main(flags)
