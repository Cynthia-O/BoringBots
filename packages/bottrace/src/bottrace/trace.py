"""Traces and spans: where a bot's time, tokens and actions go.

A *trace* covers one human request, from the moment the person asks (a click,
a command) until the bot answers. It is made of nested *spans*, one per step:
a stage, a model call, an agent turn, a tool call. Each span records when it
started (relative to the trace, for waterfall views), how long it took, whether
it failed, and attributes such as token counts. Point-in-time *events* (for
example "first token") are attached to the span they happened in.

The shapes follow OpenTelemetry's, so traces could be exported later, but
nothing here needs a collector or the network: spans are written to a local,
append-only SQLite file as each one ends.

Library code marks steps with `span(...)`. Outside an active trace, `span`
does nothing, so code can be traced or not without changing.
"""

import functools
import json
import os
import sqlite3
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS spans (
    id           INTEGER PRIMARY KEY,
    trace_id     TEXT NOT NULL,   -- one human request, start to answer
    span_id      TEXT NOT NULL,
    parent_id    TEXT,            -- NULL for the trace's root span
    bot          TEXT NOT NULL,
    name         TEXT NOT NULL,
    kind         TEXT NOT NULL,   -- request, stage, llm, turn, tool, step, wait
    started_at   TEXT NOT NULL,   -- wall-clock start, UTC, ISO 8601
    offset_ms    REAL NOT NULL,   -- start, measured from the start of the trace
    duration_ms  REAL NOT NULL,
    status       TEXT NOT NULL,   -- ok or error
    error        TEXT,
    attributes   TEXT NOT NULL,   -- JSON object
    events       TEXT NOT NULL    -- JSON list of {"name", "offset_ms", ...}
);
CREATE INDEX IF NOT EXISTS spans_by_trace ON spans (trace_id);
"""

KINDS = ("request", "stage", "llm", "turn", "tool", "step", "wait")  # wait: on the person


def default_path() -> Path:
    configured = os.environ.get("BORINGBOTS_TRACES")
    if configured:
        return Path(configured).expanduser()
    return Path.home() / ".boringbots" / "traces.sqlite"


@dataclass
class Span:
    trace: "_Trace"
    name: str
    kind: str
    parent_id: str | None
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    attributes: dict = field(default_factory=dict)
    events: list = field(default_factory=list)
    started_perf: float = field(default_factory=time.perf_counter)
    started_at: str = field(
        default_factory=lambda: datetime.now(UTC).isoformat(timespec="milliseconds")
    )

    @property
    def trace_id(self) -> str:
        return self.trace.trace_id

    def set(self, **attributes) -> None:
        """Add or update attributes (token counts, outcomes, ...)."""
        self.attributes.update(attributes)

    def add(self, key: str, amount: float = 1) -> None:
        """Add to a numeric attribute, starting from zero."""
        self.attributes[key] = self.attributes.get(key, 0) + amount

    def event(self, name: str, once: bool = False, **attributes) -> None:
        """Record a moment, timed from the start of the trace. once: keep only the first."""
        if once and any(e["name"] == name for e in self.events):
            return
        self.events.append({"name": name, "offset_ms": self.trace.elapsed_ms(), **attributes})


class _NoSpan:
    """Stands in for a span when nothing is being traced. Every call does nothing."""

    trace_id = None
    attributes: dict = {}

    def set(self, **attributes) -> None:
        pass

    def add(self, key: str, amount: float = 1) -> None:
        pass

    def event(self, name: str, once: bool = False, **attributes) -> None:
        pass


NO_SPAN = _NoSpan()
_current: ContextVar[Span | None] = ContextVar("bottrace_current_span", default=None)


class _Trace:
    def __init__(self, bot: str, store: "TraceStore"):
        self.trace_id = uuid.uuid4().hex
        self.bot = bot
        self.store = store
        self.started_perf = time.perf_counter()

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_perf) * 1000


class Tracer:
    """Starts traces for one bot. Spans go to the shared local trace file."""

    def __init__(self, bot: str, path: Path | None = None):
        self.bot = bot
        self.path = path

    @contextmanager
    def trace(self, name: str, **attributes) -> Iterator[Span]:
        """Trace one human request. Nested `span(...)` calls become its steps."""
        store = TraceStore(self.path or default_path())
        tr = _Trace(self.bot, store)
        root = Span(tr, name, "request", None, attributes=dict(attributes))
        root.started_perf = tr.started_perf
        token = _current.set(root)
        try:
            yield root
        except BaseException as e:
            _finish(root, e)
            raise
        else:
            _finish(root, None)
        finally:
            _current.reset(token)
            store.close()


@contextmanager
def span(name: str, kind: str = "step", **attributes) -> Iterator[Span | _NoSpan]:
    """Time one step inside the current trace. Does nothing outside a trace."""
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {', '.join(KINDS)}, got {kind!r}")
    parent = _current.get()
    if parent is None:
        yield NO_SPAN
        return
    child = Span(parent.trace, name, kind, parent.span_id, attributes=dict(attributes))
    token = _current.set(child)
    try:
        yield child
    except BaseException as e:
        _finish(child, e)
        raise
    else:
        _finish(child, None)
    finally:
        _current.reset(token)


def traced(name: str, kind: str = "step"):
    """Decorate a function so each call is a span. Inside, current_span().set(...)
    adds attributes. Does nothing outside a trace."""

    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            with span(name, kind=kind):
                return fn(*args, **kwargs)

        return inner

    return wrap


def current_span() -> Span | _NoSpan:
    """The innermost span in progress, or a do-nothing stand-in."""
    return _current.get() or NO_SPAN


def current_trace_id() -> str | None:
    s = _current.get()
    return s.trace_id if s else None


def finished_spans() -> tuple[list[dict], float]:
    """The spans the current trace has finished so far, and the time since it
    started (ms), for showing a request's timing before it has ended."""
    s = _current.get()
    if s is None:
        return [], 0.0
    return s.trace.store.spans(s.trace_id), s.trace.elapsed_ms()


def _finish(s: Span, error: BaseException | None) -> None:
    now = time.perf_counter()
    s.trace.store.write(
        {
            "trace_id": s.trace_id,
            "span_id": s.span_id,
            "parent_id": s.parent_id,
            "bot": s.trace.bot,
            "name": s.name,
            "kind": s.kind,
            "started_at": s.started_at,
            "offset_ms": round((s.started_perf - s.trace.started_perf) * 1000, 3),
            "duration_ms": round((now - s.started_perf) * 1000, 3),
            "status": "error" if error else "ok",
            "error": f"{type(error).__name__}: {error}" if error else None,
            "attributes": json.dumps(s.attributes, default=str, ensure_ascii=False),
            "events": json.dumps(s.events, default=str, ensure_ascii=False),
        }
    )


class TraceStore:
    """The local trace file. Rows are only ever added."""

    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.executescript(SCHEMA)

    def write(self, row: dict) -> None:
        columns = ", ".join(row)
        marks = ", ".join(f":{k}" for k in row)
        with self._conn:  # committed per span, so a crash keeps what finished
            self._conn.execute(f"INSERT INTO spans ({columns}) VALUES ({marks})", row)

    def spans(self, trace_id: str) -> list[dict]:
        return _rows(
            self._conn, "SELECT * FROM spans WHERE trace_id = ? ORDER BY offset_ms", (trace_id,)
        )

    def close(self) -> None:
        self._conn.close()


def read_traces(path: Path | None = None, limit: int = 50) -> list[list[dict]]:
    """The most recent traces, newest first, each as its list of spans. Read-only."""
    path = path or default_path()
    if not path.is_file():
        return []
    conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
    try:
        ids = [
            r["trace_id"]
            for r in _rows(
                conn,
                "SELECT trace_id FROM spans WHERE parent_id IS NULL "
                "ORDER BY started_at DESC LIMIT ?",
                (limit,),
            )
        ]
        return [
            _rows(conn, "SELECT * FROM spans WHERE trace_id = ? ORDER BY offset_ms", (t,))
            for t in ids
        ]
    finally:
        conn.close()


def _rows(conn, sql: str, params: tuple) -> list[dict]:
    cursor = conn.execute(sql, params)
    names = [d[0] for d in cursor.description]
    out = []
    for values in cursor:
        row = dict(zip(names, values, strict=True))
        for key in ("attributes", "events"):
            if key in row:
                row[key] = json.loads(row[key])
        out.append(row)
    return out
