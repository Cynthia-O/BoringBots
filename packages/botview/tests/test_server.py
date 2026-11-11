import http.client
import json

import pytest

from botview import (
    Choice,
    Confirm,
    LiveServer,
    Option,
    Page,
    Reply,
    Section,
    Slider,
    Text,
    TextField,
)


class EchoApp:
    title = "Echo"
    subtitle = "test app"
    facts = [("Folder", "/tmp/x")]
    run_label = "Go"
    controls = [
        Slider("threshold", "Threshold", 0.1, 1.0, 0.01, default=0.6),
        Choice("size", "Size", [Option("s", "Small"), Option("l", "Large")], default="s"),
        TextField("folder", "Folder", default="/tmp/x"),
    ]

    def __init__(self):
        self.calls = []

    def handle(self, action, values, data):
        self.calls.append((action, values, data))
        if action == "run":
            return Reply(
                page=Page("Result", sections=[Section(f"size {values['size']}")]),
                confirm=Confirm("Sure?", "Yes", "again", {"token": "abc"}),
            )
        if action == "boom":
            raise RuntimeError("bad")
        return Reply(status="done")

    def footer(self):
        return [Section("Tally", blocks=[Text(f"{len(self.calls)} calls")])]


@pytest.fixture
def live():
    server = LiveServer(EchoApp())
    server.start()
    yield server
    server.stop()


def request(live, method, path, body=None, key=True, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", live.port, timeout=5)
    headers = {"Host": host or f"127.0.0.1:{live.port}", "Content-Type": "application/json"}
    if key:
        headers["X-Botview-Key"] = live.key
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    response = conn.getresponse()
    return response.status, response.read().decode()


GOOD = {"threshold": "0.6", "size": "l", "folder": "  /tmp/y  "}


def test_page_needs_the_session_key(live):
    assert request(live, "GET", "/")[0] == 403
    assert request(live, "GET", "/?key=wrong")[0] == 403
    status, html = request(live, "GET", f"/?key={live.key}")
    assert status == 200
    assert 'type="range"' in html and "default" in html and "0 calls" in html


def test_other_host_names_are_refused(live):
    assert request(live, "GET", f"/?key={live.key}", host="evil.example:80")[0] == 403
    assert request(live, "POST", "/api/action", {"action": "run"}, host="evil.example")[0] == 403


def test_actions_need_the_key_header(live):
    status, _ = request(live, "POST", "/api/action", {"action": "run", "values": GOOD}, key=False)
    assert status == 403
    assert live.app.calls == []


def test_run_returns_page_confirm_and_footer(live):
    status, body = request(live, "POST", "/api/action", {"action": "run", "values": GOOD})
    reply = json.loads(body)
    assert status == 200
    assert "size l" in reply["html"]
    assert reply["confirm"] == {
        "text": "Sure?",
        "button": "Yes",
        "action": "again",
        "data": {"token": "abc"},
        "cancel": "Cancel",
    }
    assert "1 calls" in reply["footer"]
    assert live.app.calls == [("run", {"threshold": 0.6, "size": "l", "folder": "/tmp/y"}, {})]


def test_values_are_checked_against_the_controls(live):
    for bad in (
        {**GOOD, "threshold": "2"},
        {**GOOD, "size": "xl"},
        {**GOOD, "folder": None},
        {**GOOD, "folder": "x" * 1001},
    ):
        reply = json.loads(
            request(live, "POST", "/api/action", {"action": "run", "values": bad})[1]
        )
        assert reply["error"]
    assert live.app.calls == []


def test_errors_in_the_app_are_reported_not_fatal(live, capsys):
    reply = json.loads(request(live, "POST", "/api/action", {"action": "boom", "values": GOOD})[1])
    assert "RuntimeError: bad" in reply["error"]
    status, _ = request(live, "GET", f"/?key={live.key}")
    assert status == 200  # still serving


def test_defaults_are_marked():
    from botview.controls import controls_html

    html = controls_html(EchoApp.controls)
    assert 'class="toggle is-default"' in html  # "Small"
    assert 'data-default="0.6"' in html
    assert 'class="control textfield" data-default="/tmp/x"' in html
