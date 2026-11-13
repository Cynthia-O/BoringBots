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

    def __iter__(self):
        """Events as the API streams them: a start, then the text in small pieces."""
        yield SimpleNamespace(type="message_start")
        text = "".join(b.text for b in self.message.content if b.type == "text")
        for i in range(0, len(text), 7):
            yield SimpleNamespace(type="text", text=text[i : i + 7])


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
    from dupebot.ui import DEFAULT_FOLDER

    assert DupeBotApp().controls[0].default == DEFAULT_FOLDER  # typed in, ready to scan


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


# --- the suggestions agent, end to end through the app ---


class AgentClient(Client):
    """Analysis flags the v2 file as an earlier version; the agent then compares
    the two files and suggests deleting v2."""

    def __init__(self):
        super().__init__()
        self.agent_turns = 0

    def _stream(self, **request):
        self.requests.append(request)
        usage = SimpleNamespace(
            input_tokens=500,
            output_tokens=100,
            output_tokens_details=SimpleNamespace(thinking_tokens=40),
        )
        if "tools" not in request:  # the analysis call
            [cluster] = json.loads(request["messages"][0]["content"])["clusters"]
            files = [
                {
                    "path": f["path"],
                    "role": "earlier_version" if "v2" in f["path"] else "current_version",
                }
                for f in cluster["files"]
            ]
            answer = {
                "clusters": [
                    {
                        "cluster_id": cluster["cluster_id"],
                        "groups": [
                            {
                                "files": files,
                                "reason": "v2 then FINAL",
                                "relationship": "version_series",
                                "check_before_deleting": "",
                            }
                        ],
                    }
                ]
            }
            content = [SimpleNamespace(type="text", text=json.dumps(answer))]
            return Stream(SimpleNamespace(stop_reason="end_turn", usage=usage, content=content))
        self.agent_turns += 1
        if self.agent_turns == 1:
            call = SimpleNamespace(
                type="tool_use",
                id="t1",
                name="compare_files",
                input={"path_a": "Budget Review_v2.docx", "path_b": "FINAL_Budget Review.docx"},
            )
            return Stream(SimpleNamespace(stop_reason="tool_use", usage=usage, content=[call]))
        cid = json.loads(self.requests[1]["messages"][0]["content"])["clusters"][0]["cluster_id"]
        final = {
            "suggestions": [
                {
                    "cluster_id": cid,
                    "delete": "Budget Review_v2.docx",
                    "keep": "FINAL_Budget Review.docx",
                    "reason": "Same bytes.",
                }
            ],
            "summary": "One exact copy.",
        }
        content = [SimpleNamespace(type="text", text=json.dumps(final))]
        return Stream(SimpleNamespace(stop_reason="end_turn", usage=usage, content=content))


def test_send_streams_clusters_then_agent_progress_then_reorders(corpus, DEFAULTS):
    from botview import render

    app = DupeBotApp(client=AgentClient())
    assert [s.state for s in app.stages()] == ["todo", "todo", "todo"]
    updates, stages_seen = [], []

    def record(status=None, page=None):
        updates.append((status, page))
        stages_seen.append(tuple(s.state for s in app.stages()))

    app.use_updates(record)
    scanned = app.handle("run", DEFAULTS, {})
    assert [s.state for s in app.stages()] == ["done", "todo", "todo"]
    reply = app.handle("send", DEFAULTS, dict(scanned.confirm.data))

    # The stages advance in order: analysis in progress, then the agent, then all done.
    assert stages_seen[0] == ("done", "active", "todo")
    assert ("done", "done", "active") in stages_seen
    assert [s.state for s in app.stages()] == ["done", "done", "done"]
    assert "One exact copy." in app.stages()[2].detail

    pages = [render(page) for _, page in updates if page is not None]
    statuses = [status for status, _ in updates if status]
    # First every cluster waits, then answers fill in.
    assert "waiting for the model" in pages[0]
    assert any("Clusters answered</dt><dd>1 of 1" in html for html in pages)
    assert any("Analysing: 1 of 1 clusters answered" in s for s in statuses)
    # Then the agent reports its turns and checks.
    assert any(s.startswith("Prioritization agent: turn 1") for s in statuses)
    assert any(
        "comparing Budget Review_v2.docx with FINAL_Budget Review.docx" in s for s in statuses
    )

    page = reply.page
    html = render(page)
    assert reply.error is None and page.title == "DupeBot results"
    assert "priority 1" in html and "Suggested: delete Budget Review_v2.docx" in html
    assert "identical contents (checked)" in html
    assert "Prioritization agent:" not in html  # in the live app the stages say it instead
    [saved] = (corpus.parent / "pages").glob("*dupebot-results*.html")
    saved_html = saved.read_text()  # no stages on a saved copy, so it keeps the notices
    assert "DupeBot itself never deletes" in saved_html and "One exact copy." in saved_html
    assert "What the prioritization agent checked" in html
    assert len(reveal_buttons(page)) == 4  # delete/keep in the pick, plus both files below it
    # One analysis call plus two agent turns, all in this run's tally.
    assert app.tally.last.calls == 3 and app.tally.last.thinking_tokens == 120


def test_confirm_explains_what_the_agent_may_do(corpus, DEFAULTS):
    text = DupeBotApp(client=Client()).handle("run", DEFAULTS, {}).confirm.text
    assert "compare two files on this Mac" in text and "cannot change any file" in text


# --- tracing ---


def test_send_is_traced_step_by_step(corpus, DEFAULTS, tmp_path):
    from bottrace import read_traces, summarize
    from dupebot.calllog import CallLog

    app = DupeBotApp(client=AgentClient())
    reply = app.handle("send", DEFAULTS, dict(app.handle("run", DEFAULTS, {}).confirm.data))
    send, scan_trace = read_traces()  # newest first
    names = [s["name"] for s in send]
    for expected in (
        "send to model",
        "analyze",
        "analyze call",
        "prioritize",
        "turn 1",
        "compare_files",
        "turn 2",
        "suggest call",
    ):
        assert expected in names
    assert {"walk folder", "cluster names", "build report"} <= {s["name"] for s in scan_trace}
    s = summarize(send)
    assert (s.llm_calls, s.turns, s.tool_calls) == (3, 2, 1)
    assert s.thinking_tokens == 120 and s.cost_usd > 0
    assert len(s.first_token_ms) == 3
    assert [m[0] for m in s.milestones] == ["first_result_shown", "analysis_done"]
    # The call log's rows share the trace's id, so the two can be joined.
    with CallLog() as log:
        assert {r["run_id"] for r in log.rows()} == {send[0]["trace_id"]}
    from botview import render

    assert "How long this took" in render(reply.page)
