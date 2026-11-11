"""A live botview app: a local page with settings controls that runs the bot.

The bot supplies an App: its controls, and a handle() method that turns an
action plus the chosen settings into a Reply (a results page, a question to
confirm, or an error). botview serves the page, sends the user's clicks to
handle(), and shows what comes back. The server lives as long as the session:
stopping it (Ctrl+C) ends the session.

Safety: the server listens only on this machine (127.0.0.1). Every request must
carry a random key minted for this session, and requests naming another host are
refused. Together these stop other websites open in the browser from driving the
app, for example to start paid model calls.
"""

import html
import json
import secrets
import sys
import threading
import traceback
import webbrowser
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Protocol
from urllib.parse import parse_qs, urlsplit

from botview.controls import (
    CONTROLS_CSS,
    CONTROLS_JS,
    Control,
    controls_html,
    parse_values,
    to_json_script,
)
from botview.page import CSS, Page, Section, document, facts_html, render_body, section_html

MAX_REQUEST_BYTES = 1_000_000


@dataclass(frozen=True)
class Confirm:
    """A question shown before an action, such as one that costs money.
    Pressing the button runs action with data passed back unchanged."""

    text: str
    button: str
    action: str
    data: Mapping[str, object] = field(default_factory=dict)
    cancel: str = "Cancel"


@dataclass(frozen=True)
class Reply:
    page: Page | None = None  # replaces the results area when given
    confirm: Confirm | None = None
    error: str | None = None
    status: str | None = None  # a short line next to the run button


class App(Protocol):
    title: str
    subtitle: str
    facts: Sequence[tuple[str, str]]
    controls: Sequence[Control]
    run_label: str  # the main button, which sends action "run"

    def handle(self, action: str, values: dict, data: dict) -> Reply: ...

    def footer(self) -> Sequence[Section]: ...


class LiveServer:
    """Serves one App on 127.0.0.1. Use start() in tests, serve() for real."""

    def __init__(self, app: App, port: int = 0):
        self.app = app
        self.key = secrets.token_urlsafe(24)
        self._busy = threading.Lock()
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), _handler_for(self))
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?key={self.key}"

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        return thread

    def serve(self, open_browser: bool = True) -> None:
        print(f"{self.app.title} is running at {self.url}", file=sys.stderr)
        print("Press Ctrl+C here to stop it and end the session.", file=sys.stderr)
        if open_browser and not open_in_browser_url(self.url):
            print("Could not open a browser; open the address above yourself.", file=sys.stderr)
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nSession ended.", file=sys.stderr)
        finally:
            self.httpd.server_close()

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # --- what the handler calls ---

    def shell(self) -> str:
        app = self.app
        head = [f'<header class="app-head"><h1>{_e(app.title)}</h1>']
        if app.subtitle:
            head.append(f'<p class="subtitle">{_e(app.subtitle)}</p>')
        if app.facts:
            head.append(facts_html(app.facts))
        head.append("</header>")
        body = "\n".join(
            [
                *head,
                '<form id="controls" class="controls">',
                controls_html(app.controls),
                '<div class="actions">'
                f'<button type="submit" class="primary">{_e(app.run_label)}</button>'
                '<span class="status" id="status"></span></div>',
                "</form>",
                '<div id="error" class="error" hidden></div>',
                '<div id="confirm" class="confirm" hidden></div>',
                '<div id="results"></div>',
                f'<footer id="footer" class="app-footer">{self.footer_html()}</footer>',
            ]
        )
        script = to_json_script("KEY", self.key) + CONTROLS_JS + _APP_JS
        return document(app.title, body, script, body_class="live").replace(
            f"<style>{CSS}</style>", f"<style>{CSS}{CONTROLS_CSS}</style>"
        )

    def footer_html(self) -> str:
        return "\n".join(section_html(s) for s in self.app.footer())

    def act(self, request: dict) -> dict:
        if not self._busy.acquire(blocking=False):
            return {"error": "Still working on the last request. Please wait for it to finish."}
        try:
            try:
                values = parse_values(self.app.controls, request.get("values") or {})
            except ValueError as e:
                return {"error": str(e)}
            action = request.get("action")
            if not isinstance(action, str):
                return {"error": "No action given."}
            try:
                reply = self.app.handle(action, values, dict(request.get("data") or {}))
            except Exception as e:
                traceback.print_exc()
                reply = Reply(error=f"Something went wrong: {type(e).__name__}: {e}")
            out: dict = {"footer": self.footer_html(), "error": reply.error, "status": reply.status}
            if reply.page is not None:
                out["html"] = render_body(reply.page)
            if reply.confirm is not None:
                c = reply.confirm
                out["confirm"] = {
                    "text": c.text,
                    "button": c.button,
                    "action": c.action,
                    "data": dict(c.data),
                    "cancel": c.cancel,
                }
            return out
        finally:
            self._busy.release()


def serve(app: App, open_browser: bool = True) -> None:
    """Run app until Ctrl+C. Opens it in the browser unless told not to."""
    LiveServer(app).serve(open_browser)


def open_in_browser_url(url: str) -> bool:
    return webbrowser.open(url)


def _handler_for(live: LiveServer) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):  # keep the terminal quiet
            pass

        def _refuse(self, code: int, text: str) -> None:
            self._send(code, "text/plain; charset=utf-8", text.encode())

        def _send(self, code: int, kind: str, body: bytes) -> None:
            self.send_response(code)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def _host_ok(self) -> bool:
            # Refusing other host names blocks DNS-rebinding attacks.
            return self.headers.get("Host") in (f"127.0.0.1:{live.port}", f"localhost:{live.port}")

        def do_GET(self):
            if not self._host_ok():
                return self._refuse(403, "Forbidden")
            parts = urlsplit(self.path)
            if parts.path == "/favicon.ico":
                return self._refuse(404, "Not found")
            key = parse_qs(parts.query).get("key", [""])[0]
            if parts.path != "/" or not secrets.compare_digest(key, live.key):
                return self._refuse(403, "Open the address printed in the terminal.")
            self._send(200, "text/html; charset=utf-8", live.shell().encode())

        def do_POST(self):
            if not self._host_ok():
                return self._refuse(403, "Forbidden")
            # A custom header cannot be sent cross-site without a CORS preflight,
            # which this server never approves.
            if not secrets.compare_digest(self.headers.get("X-Botview-Key", ""), live.key):
                return self._refuse(403, "Forbidden")
            if self.path != "/api/action":
                return self._refuse(404, "Not found")
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_REQUEST_BYTES:
                return self._refuse(413, "Too large")
            try:
                request = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(request, dict):
                    raise ValueError
            except ValueError:
                return self._refuse(400, "Bad request")
            body = json.dumps(live.act(request)).encode()
            self._send(200, "application/json", body)

    return Handler


def _e(text: str) -> str:
    return html.escape(text, quote=True)


_APP_JS = """
const form = document.getElementById('controls');
const statusEl = document.getElementById('status');
const errorEl = document.getElementById('error');
const confirmEl = document.getElementById('confirm');
const resultsEl = document.getElementById('results');
const footerEl = document.getElementById('footer');

function values() {
  const out = {};
  for (const el of form.elements) {
    if (!el.name) continue;
    if (el.type === 'radio') { if (el.checked) out[el.name] = el.value; }
    else out[el.name] = el.value;
  }
  return out;
}

function busy(on) {
  for (const b of document.querySelectorAll('button')) b.disabled = on;
}

async function act(action, data, keepConfirm) {
  errorEl.hidden = true;
  if (!keepConfirm) confirmEl.hidden = true;
  busy(true);
  const started = Date.now();
  const tick = () => {
    statusEl.textContent = `Working… ${Math.round((Date.now() - started) / 1000)}s`;
  };
  tick();
  const timer = setInterval(tick, 1000);
  try {
    const response = await fetch('/api/action', {
      method: 'POST',
      headers: {'Content-Type': 'application/json', 'X-Botview-Key': KEY},
      body: JSON.stringify({action, values: values(), data: data || {}}),
    });
    if (!response.ok) throw new Error(`the bot answered ${response.status}`);
    show(await response.json());
  } catch (e) {
    show({error: `Could not reach the bot (${e.message}). Is it still running in the terminal?`});
  } finally {
    clearInterval(timer);
    busy(false);
  }
}

function show(reply) {
  statusEl.textContent = reply.status || '';
  if (reply.error) { errorEl.textContent = reply.error; errorEl.hidden = false; }
  if (reply.html !== undefined) resultsEl.innerHTML = reply.html;
  if (reply.footer !== undefined) footerEl.innerHTML = reply.footer;
  if (reply.confirm) {
    const c = reply.confirm;
    confirmEl.replaceChildren();
    const p = document.createElement('p');
    p.textContent = c.text;
    const go = document.createElement('button');
    go.className = 'primary';
    go.textContent = c.button;
    go.addEventListener('click', () => act(c.action, c.data));
    const no = document.createElement('button');
    no.type = 'button';
    no.textContent = c.cancel;
    no.addEventListener('click', () => {
      confirmEl.hidden = true;
      statusEl.textContent = 'Not sent.';
    });
    const row = document.createElement('div');
    row.className = 'actions';
    row.append(go, no);
    confirmEl.append(p, row);
    confirmEl.hidden = false;
  }
}

form.addEventListener('submit', (event) => { event.preventDefault(); act('run'); });
// Buttons inside results (botview Action cells). A pending question stays open.
document.addEventListener('click', (event) => {
  const button = event.target.closest('.cell-action');
  if (button) act(button.dataset.action, JSON.parse(button.dataset.payload), true);
});
"""
