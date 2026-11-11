import json
from types import SimpleNamespace

import pytest

from dupebot.ui import DupeBotApp, TokenTally

SETTINGS = {"threshold": 0.6, "model": "claude-haiku-5-5", "effort": "default"}


class Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class Client:
    """Answers with every file in one unrelated group, reporting thinking tokens."""

    def __init__(self, stop_reason="end_turn"):
        self.requests = []
        self.stop_reason = stop_reason
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        self.requests.append(request)
        clusters = json.loads(request["messages"][0]["content"])["clusters"]
        answer = {
            "clusters": [
                {
                    "cluster_id": c["cluster_id"],
                    "groups": [
                        {
                            "files": [{"path": f["path"], "role": "member"} for f in c["files"]],
                            "reason": "r",
                            "relationship": "unrelated",
                            "confidence": "low",
                            "check_before_deleting": "",
                        }
                    ],
                }
                for c in clusters
            ]
        }
        usage = SimpleNamespace(
            input_tokens=1000,
            output_tokens=300,
            output_tokens_details=SimpleNamespace(thinking_tokens=200),
        )
        return Stream(
            SimpleNamespace(
                stop_reason=self.stop_reason,
                usage=usage,
                content=[SimpleNamespace(type="text", text=json.dumps(answer))],
            )
        )


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "corpus"
    root.mkdir()
    for name in ("Budget Review_v2.docx", "FINAL_Budget Review.docx"):
        (root / name).write_bytes(b"x" * 300_000)
    return root


@pytest.fixture
def DEFAULTS(corpus):
    return {**SETTINGS, "folder": str(corpus)}


def test_scan_shows_clusters_and_asks_before_sending(corpus, DEFAULTS):
    client = Client()
    reply = DupeBotApp(corpus, client=client).handle("run", DEFAULTS, {})
    assert reply.page.title == "Clusters found"
    assert "Send 1 clusters (2 files) to claude-haiku-5-5" in reply.confirm.text
    assert client.requests == []  # nothing sent yet


def test_send_analyses_and_counts_tokens(corpus, DEFAULTS):
    app = DupeBotApp(corpus, client=Client())
    scanned = app.handle("run", {**DEFAULTS, "effort": "low"}, {})
    reply = app.handle("send", DEFAULTS, dict(scanned.confirm.data))
    assert reply.error is None and reply.page.title == "DupeBot results"
    s = app.tally.session
    assert (s.calls, s.input_tokens, s.thinking_tokens, s.response_tokens) == (1, 1000, 200, 100)
    # Haiku: $0.10 in, $0.50 out per million tokens
    assert s.cost.input == pytest.approx(0.0001)
    assert s.cost.thinking == pytest.approx(0.0001) and s.cost.response == pytest.approx(0.00005)
    assert app.tally.last.output_tokens == 300
    app.handle("send", DEFAULTS, dict(app.handle("run", DEFAULTS, {}).confirm.data))
    assert app.tally.session.input_tokens == 2000 and app.tally.last.input_tokens == 1000


def test_send_uses_the_settings_from_the_scan(corpus, DEFAULTS):
    client = Client()
    app = DupeBotApp(corpus, client=client)
    scanned = app.handle("run", {**DEFAULTS, "model": "claude-opus-5-5", "effort": "high"}, {})
    app.handle("send", DEFAULTS, dict(scanned.confirm.data))  # controls changed since
    assert client.requests[0]["model"] == "claude-opus-5-5"
    assert client.requests[0]["output_config"]["effort"] == "high"


def test_an_old_scan_cannot_be_sent(corpus, DEFAULTS):
    app = DupeBotApp(corpus, client=Client())
    first = app.handle("run", DEFAULTS, {})
    app.handle("run", DEFAULTS, {})
    assert "no longer current" in app.handle("send", DEFAULTS, dict(first.confirm.data)).error


def test_billed_tokens_of_unusable_answers_are_counted(corpus, DEFAULTS):
    app = DupeBotApp(corpus, client=Client(stop_reason="max_tokens"))
    reply = app.handle("send", DEFAULTS, dict(app.handle("run", DEFAULTS, {}).confirm.data))
    assert "cut off" in reply.error
    assert app.tally.session.output_tokens == 300


def test_no_clusters_means_nothing_to_send(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    reply = DupeBotApp(client=Client()).handle("run", {**SETTINGS, "folder": str(empty)}, {})
    assert reply.confirm is None and "No clusters" in reply.status


def test_tally_starts_empty_each_session(corpus):
    assert DupeBotApp(corpus).tally.session.calls == 0
    section = TokenTally().section(corpus / "log.sqlite")
    assert section.title == "Token usage and estimated cost"
    fine_print = [b for b in section.blocks if getattr(b, "fine_print", False)]
    assert len(fine_print) == 1 and "Prices last checked" in fine_print[0].text


def test_flags_preselect_controls(corpus):
    app = DupeBotApp(corpus, threshold=0.7, model="claude-sonnet-5-5", effort="max")
    _, slider, model, effort = app.controls
    assert (slider.selected, model.selected, effort.selected) == (0.7, "claude-sonnet-5-5", "max")
    assert (slider.default, model.default, effort.default) == (0.6, "claude-haiku-5-5", "default")


def test_folder_is_an_editable_field_starting_on_the_cli_folder(corpus):
    folder = DupeBotApp(corpus).controls[0]
    assert (folder.name, folder.default) == ("folder", str(corpus))
    assert DupeBotApp().controls[0].default == ""


def test_scan_uses_the_folder_typed_in_the_page(tmp_path, corpus):
    other = tmp_path / "other"
    other.mkdir()
    app = DupeBotApp(other, client=Client())  # started on an empty folder
    reply = app.handle("run", {**SETTINGS, "folder": str(corpus)}, {})
    assert reply.confirm is not None and str(corpus) in str(reply.page.facts)


@pytest.mark.parametrize(
    "folder, message",
    [("", "Enter the folder"), ("relative/path", "full path"), ("/no/such/folder", "Not a folder")],
)
def test_bad_folders_are_explained(folder, message):
    reply = DupeBotApp(client=Client()).handle("run", {**SETTINGS, "folder": folder}, {})
    assert message in reply.error


def test_home_folder_shorthand_works(monkeypatch, corpus):
    monkeypatch.setenv("HOME", str(corpus.parent))
    reply = DupeBotApp(client=Client()).handle("run", {**SETTINGS, "folder": "~/corpus"}, {})
    assert reply.error is None and reply.confirm is not None


# --- Show in Finder ---


def reveal_buttons(page):
    from botview import Action

    return [
        cell
        for section in page.sections
        for block in section.blocks
        for table in ([block] if hasattr(block, "rows") else getattr(block, "blocks", []))
        if hasattr(table, "rows")
        for row in table.rows
        for cell in row
        if isinstance(cell, Action)
    ]


@pytest.fixture
def opened(monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("dupebot.ui.subprocess.run", fake_run)
    monkeypatch.setattr("dupebot.ui.sys.platform", "darwin")
    return calls


def test_each_scanned_file_gets_a_show_in_finder_button(corpus, DEFAULTS):
    reply = DupeBotApp(client=Client()).handle("run", DEFAULTS, {})
    buttons = reveal_buttons(reply.page)
    assert len(buttons) == 2 and all(b.action == "reveal" for b in buttons)
    assert all(str(corpus) not in str(b.data) for b in buttons)  # ids, not paths


def test_results_have_buttons_too(corpus, DEFAULTS):
    app = DupeBotApp(client=Client())
    reply = app.handle("send", DEFAULTS, dict(app.handle("run", DEFAULTS, {}).confirm.data))
    assert len(reveal_buttons(reply.page)) == 2


def test_reveal_opens_finder_on_the_scanned_file(corpus, DEFAULTS, opened):
    app = DupeBotApp(client=Client())
    button = reveal_buttons(app.handle("run", DEFAULTS, {}).page)[0]
    reply = app.handle("reveal", DEFAULTS, dict(button.data))
    assert reply.error is None and "new Finder window" in reply.status
    [args] = opened
    assert args[0] == "osascript" and args[-1].startswith(str(corpus))
    assert str(corpus) not in args[2]  # the path is an argument, not part of the script


def test_reveal_falls_back_when_finder_permission_is_refused(corpus, DEFAULTS, monkeypatch):
    calls = []

    def fake_run(args, **kwargs):
        calls.append(args)
        if args[0] == "osascript":
            return SimpleNamespace(
                returncode=1, stderr="Not authorized to send Apple events (-1743)"
            )
        return SimpleNamespace(returncode=0, stderr="")

    monkeypatch.setattr("dupebot.ui.subprocess.run", fake_run)
    monkeypatch.setattr("dupebot.ui.sys.platform", "darwin")
    app = DupeBotApp(client=Client())
    button = reveal_buttons(app.handle("run", DEFAULTS, {}).page)[0]
    reply = app.handle("reveal", DEFAULTS, dict(button.data))
    assert [c[0] for c in calls] == ["osascript", "open"]
    assert reply.error is None and "Automation" in reply.status


def test_reveal_refuses_ids_it_did_not_issue(DEFAULTS, opened):
    app = DupeBotApp(client=Client())
    app.handle("run", DEFAULTS, {})
    for data in ({"file": "/etc/passwd"}, {"file": "deadbeef"}, {}):
        assert "not from a scan" in app.handle("reveal", DEFAULTS, data).error
    assert opened == []


def test_reveal_reports_a_file_that_has_gone(corpus, DEFAULTS, opened):
    app = DupeBotApp(client=Client())
    button = reveal_buttons(app.handle("run", DEFAULTS, {}).page)[0]
    for f in corpus.iterdir():
        f.unlink()  # the test's own temporary files
    assert "No longer there" in app.handle("reveal", DEFAULTS, dict(button.data)).error
    assert opened == []
