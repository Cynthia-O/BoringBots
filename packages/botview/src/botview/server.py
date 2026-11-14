"""A live botview app: a local page with settings controls that runs the bot.

The bot supplies an App: its controls, and a handle() method that turns an
action plus the chosen settings into a Reply (a results page, a question to
confirm, or an error). botview serves the page, sends the user's clicks to
handle(), and shows what comes back. The server lives as long as the session:
stopping it (Ctrl+C) ends the session.

Windows: give LiveServer a function that makes an App (rather than an App) and
every browser window opened on it gets its own App, with its own settings,
results and progress, so several experiments can run side by side. Shared
records (traces, logs) are the app's business.

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
from collections.abc import Callable, Mapping, Sequence
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
from botview.stages import STAGES_CSS, stages_html

MAX_REQUEST_BYTES = 1_000_000
MAX_WINDOWS = 20  # windows per server; each holds its own App


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

    # Optional: def stages(self) -> Sequence[Stage]
    # Numbered stages shown under the title and redrawn after every action and
    # live update, so the person can see which step the work is on.

    # Optional: def use_updates(self, update: Update) -> None
    # If the app has it, LiveServer calls it once with a function the app can
    # call during a long handle() to show progress while it works:
    # update(status="12 of 35 clusters answered", page=partial_page). The page
    # polls for these, shows the status line instead of a bare timer, and
    # redraws the results area whenever a new page arrives.


@dataclass
class _Window:
    """One browser window's own App and the progress of its running action."""

    number: int
    app: "App"
    busy: threading.Lock = field(default_factory=threading.Lock)
    # (version, status, results html, stages html), replaced as one tuple so a
    # poll from another thread always sees a whole update.
    latest: tuple[int, str, str | None, str] = (0, "", None, "")
    id: str = field(default_factory=lambda: secrets.token_urlsafe(12))


class LiveServer:
    """Serves an App on 127.0.0.1. Use start() in tests, serve() for real.

    app: an App, shared by every window, or a function that makes one, so each
    window gets its own. Subclasses can extend who may connect: handler_base
    (the request handler class), allowed_hosts() and act() are the extension
    points.
    """

    def __init__(self, app: "App | Callable[[], App]", port: int = 0):
        self.key = secrets.token_urlsafe(24)
        # A class, or a function that is not itself an App, makes one App per window.
        if isinstance(app, type) or (callable(app) and not hasattr(app, "handle")):
            self._make_app, self.multi_window = app, True
        else:
            self._make_app, self.multi_window = (lambda: app), False
        self._windows: dict[str, _Window] = {}
        self._lock = threading.Lock()
        self.default_window = self._create_window()  # requests naming no window use it
        self._default_claimed = False  # the first page opened takes the default window
        self.app = self.default_window.app  # for the title and the like
        handler = type("Handler", (self.handler_base,), {"live": self})
        self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        self.port = self.httpd.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/?key={self.key}"

    def new_window(self) -> _Window | None:
        """A window for a page being opened, or None when there are too many."""
        with self._lock:
            if not self._default_claimed or not self.multi_window:
                self._default_claimed = True
                return self.default_window  # the first page, or the only App
            if len(self._windows) >= MAX_WINDOWS:
                return None
            window = self._register()  # checked and created under one lock
        return self._hook_updates(window)

    def _create_window(self) -> _Window:
        with self._lock:
            window = self._register()
        return self._hook_updates(window)

    def _register(self) -> _Window:
        window = _Window(len(self._windows) + 1, self._make_app())
        self._windows[window.id] = window
        return window

    def _hook_updates(self, window: _Window) -> _Window:
        use_updates = getattr(window.app, "use_updates", None)
        if callable(use_updates):
            use_updates(lambda status=None, page=None: self.update(status, page, window))
        return window

    def window(self, window_id: str | None) -> _Window | None:
        """The window a request names, the default window if it names none,
        or None if it names one that does not exist."""
        if not window_id:
            return self.default_window
        return self._windows.get(window_id)

    @property
    def handler_base(self) -> type["LiveHandler"]:
        return LiveHandler

    def allowed_hosts(self) -> set[str]:
        return {f"127.0.0.1:{self.port}", f"localhost:{self.port}"}

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        return thread

    def serve(self, open_browser: bool = True) -> bool:
        """Run until Ctrl+C. Returns False if the server could not start."""
        print(f"{self.app.title} is running at {self.url}", file=sys.stderr)
        if self.multi_window:
            print(
                "Each window you open runs on its own: use the page's New window link, "
                "or open the address again.",
                file=sys.stderr,
            )
        print("Press Ctrl+C here to stop it and end the session.", file=sys.stderr)
        if open_browser and not open_in_browser_url(self.url):
            print("Could not open a browser; open the address above yourself.", file=sys.stderr)
        try:
            self.httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nSession ended.", file=sys.stderr)
        finally:
            self.httpd.server_close()
        return True

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    # --- what the handler calls ---

    def shell(
        self,
        key: str | None = None,
        notice: str | None = None,
        window: _Window | None = None,
        new_window_href: str | None = None,
    ) -> str:
        """A window's page. key: the key its requests must carry (the host's by
        default). notice: an optional line under the title. new_window_href:
        where the New window link points (the host's address by default)."""
        window = window or self.default_window
        app = window.app
        title = f"{app.title} · window {window.number}" if self.multi_window else app.title
        head = [f'<header class="app-head"><h1>{_e(title)}</h1>']
        if self.multi_window:
            href = new_window_href or f"/?key={key or self.key}"
            head.append(
                f'<p class="new-window"><a href="{_e(href)}" target="_blank" rel="noopener">'
                "+ New window</a> runs separately, with its own settings and results.</p>"
            )
        if notice:
            head.append(f'<p class="callout tone-info">{_e(notice)}</p>')
        if app.subtitle:
            head.append(f'<p class="subtitle">{_e(app.subtitle)}</p>')
        if app.facts:
            head.append(facts_html(app.facts))
        head.append("</header>")
        body = "\n".join(
            [
                *head,
                f'<div id="stages">{self.stages_html(window)}</div>',
                '<form id="controls" class="controls">',
                controls_html(app.controls),
                '<div class="actions">'
                f'<button type="submit" class="primary">{_e(app.run_label)}</button>'
                '<span class="status" id="status"></span></div>',
                "</form>",
                '<div id="error" class="error" hidden></div>',
                '<div id="confirm" class="confirm" hidden></div>',
                '<div id="results"></div>',
                f'<footer id="footer" class="app-footer">{self.footer_html(window)}</footer>',
            ]
        )
        script = (
            to_json_script("KEY", key or self.key)
            + to_json_script("WINDOW", window.id)
            + CONTROLS_JS
            + _APP_JS
        )
        return document(title, body, script, body_class="live").replace(
            f"<style>{CSS}</style>", f"<style>{CSS}{CONTROLS_CSS}{STAGES_CSS}</style>"
        )

    def stages_html(self, window: _Window | None = None) -> str:
        stages = getattr((window or self.default_window).app, "stages", None)
        return stages_html(stages()) if callable(stages) else ""

    def footer_html(self, window: _Window | None = None) -> str:
        return "\n".join(section_html(s) for s in (window or self.default_window).app.footer())

    def update(
        self, status: str | None = None, page: Page | None = None, window: _Window | None = None
    ) -> None:
        """Called by an app during handle() to show progress (see App)."""
        window = window or self.default_window
        version, old_status, old_html, _ = window.latest
        window.latest = (
            version + 1,
            old_status if status is None else str(status),
            old_html if page is None else render_body(page),
            self.stages_html(window),  # the app may have moved on a stage too
        )

    def latest(self, window: _Window | None = None) -> dict:
        version, status, html, stages = (window or self.default_window).latest
        return {"version": version, "status": status, "html": html, "stages": stages}

    def act(self, request: dict, role: str = "host", window: _Window | None = None) -> dict:
        """Run one action in a window. role says who asked; this server only has the host."""
        window = window or self.default_window
        app = window.app
        if not window.busy.acquire(blocking=False):
            return {"error": "Still working on the last request. Please wait for it to finish."}
        window.latest = (0, "", None, "")
        try:
            try:
                values = parse_values(app.controls, request.get("values") or {})
            except ValueError as e:
                return {"error": str(e)}
            action = request.get("action")
            if not isinstance(action, str):
                return {"error": "No action given."}
            try:
                reply = app.handle(action, values, dict(request.get("data") or {}))
            except Exception as e:
                traceback.print_exc()
                reply = Reply(error=f"Something went wrong: {type(e).__name__}: {e}")
            out: dict = {
                "footer": self.footer_html(window),
                "stages": self.stages_html(window),
                "error": reply.error,
                "status": reply.status,
            }
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
            window.latest = (0, "", None, "")
            window.busy.release()


def serve(app: App, open_browser: bool = True) -> None:
    """Run app until Ctrl+C. Opens it in the browser unless told not to."""
    LiveServer(app).serve(open_browser)


def open_in_browser_url(url: str) -> bool:
    return webbrowser.open(url)


class LiveHandler(BaseHTTPRequestHandler):
    """Requests to a LiveServer. `live` is set to the server for each instance.
    Subclasses extend role(), respond_json(), other_get() and other_post()."""

    live: LiveServer

    def log_message(self, format, *args):  # keep the terminal quiet
        pass

    def refuse(self, code: int, text: str) -> None:
        self.send(code, "text/plain; charset=utf-8", text.encode())

    def send(self, code: int, kind: str, body: bytes, headers: dict | None = None) -> None:
        self.send_response(code)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, code: int, page: str, headers: dict | None = None) -> None:
        self.send(code, "text/html; charset=utf-8", page.encode(), headers)

    def host_ok(self) -> bool:
        # Refusing other host names blocks DNS-rebinding attacks.
        return self.headers.get("Host") in self.live.allowed_hosts()

    def role(self) -> str | None:
        """Who sent this request, or None if it may not use the app.
        A custom header cannot be sent cross-site without a CORS preflight,
        which this server never approves."""
        key = self.headers.get("X-Botview-Key", "")
        return "host" if secrets.compare_digest(key, self.live.key) else None

    def respond_json(self, data: dict, role: str) -> None:
        text = json.dumps(data, ensure_ascii=False)
        self.send(200, "application/json; charset=utf-8", text.encode())

    def other_get(self, parts) -> bool:
        """Handle a GET this class does not know. Returns True if handled."""
        return False

    def other_post(self) -> bool:
        """Handle a POST before the key check. Returns True if handled."""
        return False

    def do_GET(self):
        if not self.host_ok():
            return self.refuse(403, "Forbidden")
        parts = urlsplit(self.path)
        if parts.path == "/favicon.ico":
            return self.refuse(404, "Not found")
        key = parse_qs(parts.query).get("key", [""])[0]
        if parts.path == "/" and secrets.compare_digest(key, self.live.key):
            window = self.live.new_window()
            if window is None:
                return self.refuse(503, "Too many windows open. Close some, or restart.")
            return self.send_html(200, self.live.shell(window=window))
        if self.other_get(parts):
            return
        return self.refuse(403, "Open the address printed in the terminal.")

    def do_POST(self):
        if not self.host_ok():
            return self.refuse(403, "Forbidden")
        if self.other_post():
            return
        role = self.role()
        if role is None:
            return self.refuse(403, "Forbidden")
        window = self.live.window(self.headers.get("X-Botview-Window"))
        if window is None:
            return self.respond_json({"error": "This window has expired. Reload the page."}, role)
        if self.path == "/api/progress":  # answered while an action runs
            return self.respond_json(self.live.latest(window), role)
        if self.path != "/api/action":
            return self.refuse(404, "Not found")
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_REQUEST_BYTES:
            return self.refuse(413, "Too large")
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(request, dict):
                raise ValueError
        except ValueError:
            return self.refuse(400, "Bad request")
        self.respond_json(self.live.act(request, role, window), role)


def _e(text: str) -> str:
    return html.escape(text, quote=True)


_APP_JS = """
const form = document.getElementById('controls');
const statusEl = document.getElementById('status');
const errorEl = document.getElementById('error');
const confirmEl = document.getElementById('confirm');
const resultsEl = document.getElementById('results');
const footerEl = document.getElementById('footer');
const stagesEl = document.getElementById('stages');

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
  let progress = '';
  let shown = 0;
  let polling = false;
  let finished = false;  // a late poll must not draw over the final answer
  const tick = async () => {
    // What the bot says it is doing, with a seconds ticker so it visibly lives.
    const seconds = `${Math.round((Date.now() - started) / 1000)}s`;
    statusEl.textContent = progress ? `${progress} · ${seconds}` : `Working… ${seconds}`;
    if (polling) return;
    polling = true;
    try {
      const r = await fetch('/api/progress', {
        method: 'POST', headers: {'X-Botview-Key': KEY, 'X-Botview-Window': WINDOW},
      });
      if (r.ok && !finished) {
        const latest = await r.json();
        if (finished) return;
        progress = latest.status || '';
        if (latest.stages && stagesEl.innerHTML !== latest.stages) {
          stagesEl.innerHTML = latest.stages;
        }
        if (latest.html && latest.version > shown) {
          shown = latest.version;
          resultsEl.innerHTML = latest.html;
          busy(true);  // buttons in the new results wait until the bot is free
        }
      }
    } catch (e) { /* the main request reports any real failure */ }
    polling = false;
  };
  tick();
  const timer = setInterval(tick, 1000);
  try {
    const response = await fetch('/api/action', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json', 'X-Botview-Key': KEY, 'X-Botview-Window': WINDOW,
      },
      body: JSON.stringify({action, values: values(), data: data || {}}),
    });
    if (!response.ok) throw new Error(`the bot answered ${response.status}`);
    const reply = await response.json();
    finished = true;
    show(reply);
  } catch (e) {
    finished = true;
    show({error: `Could not reach the bot (${e.message}). Is it still running in the terminal?`});
  } finally {
    finished = true;
    clearInterval(timer);
    busy(false);
  }
}

function show(reply) {
  statusEl.textContent = reply.status || '';
  if (reply.error) { errorEl.textContent = reply.error; errorEl.hidden = false; }
  if (reply.html !== undefined) resultsEl.innerHTML = reply.html;
  if (reply.footer !== undefined) footerEl.innerHTML = reply.footer;
  if (reply.stages !== undefined) stagesEl.innerHTML = reply.stages;
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
