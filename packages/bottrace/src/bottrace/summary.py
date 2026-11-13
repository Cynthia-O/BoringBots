"""Make sense of a trace: totals, per-turn figures, and where the time went.

Time is attributed by *self time*: a span's own duration minus the time spent
in its child spans. Summed by kind, self time says how much of the wait was
model calls, tool calls, or the bot's own work, and the parts add up to the
whole request.
"""

from collections import defaultdict
from dataclasses import dataclass, field

# Labels for the kinds of work time is attributed to.
KIND_LABELS = {
    "llm": "Model calls",
    "tool": "Tool calls",
    "wait": "Waiting for you",  # e.g. a confirmation prompt; not the bot's latency
    "own": "Bot's own work",  # request, stage, turn and step self time
}


@dataclass
class TurnRow:
    name: str
    duration_ms: float
    actions: list[str]
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cost_usd: float = 0.0
    stop_reason: str | None = None


@dataclass
class TraceSummary:
    trace_id: str
    bot: str
    name: str
    started_at: str
    total_ms: float
    status: str
    llm_calls: int = 0
    turns: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    errors: list[str] = field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    time_by_kind: dict[str, float] = field(default_factory=dict)  # self time, ms
    turn_rows: list[TurnRow] = field(default_factory=list)
    milestones: list[tuple[str, float]] = field(default_factory=list)  # (event, ms from start)
    first_token_ms: list[float] = field(default_factory=list)  # per model call


@dataclass(frozen=True)
class WaterfallRow:
    label: str
    depth: int
    start_ms: float
    duration_ms: float
    kind: str
    detail: str
    error: bool = False


def summarize(spans: list[dict], now_ms: float | None = None) -> TraceSummary:
    """Summarize one trace's spans. For a trace still running (no root span yet),
    pass now_ms, the time since it started, to use as its length so far."""
    root = next((s for s in spans if s["parent_id"] is None), None)
    first = spans[0] if spans else {}
    total = root["duration_ms"] if root else (now_ms or _end(spans))
    out = TraceSummary(
        trace_id=first.get("trace_id", ""),
        bot=first.get("bot", ""),
        name=root["name"] if root else "(in progress)",
        started_at=root["started_at"] if root else first.get("started_at", ""),
        total_ms=total,
        status=root["status"] if root else "running",
    )
    children = defaultdict(list)
    for s in spans:
        children[s["parent_id"]].append(s)
    for s in spans:
        a = s["attributes"]
        if s["status"] == "error":
            out.errors.append(f"{s['name']}: {s['error']}")
        if s["kind"] == "llm":
            out.llm_calls += 1
            out.input_tokens += a.get("input_tokens") or 0
            out.output_tokens += a.get("output_tokens") or 0
            out.thinking_tokens += a.get("thinking_tokens") or 0
            out.cache_read_tokens += a.get("cache_read_tokens") or 0
            out.cost_usd += a.get("cost_usd") or 0
            first_event = next((e for e in s["events"] if e["name"] == "first_event"), None)
            if first_event:
                out.first_token_ms.append(first_event["offset_ms"] - s["offset_ms"])
        elif s["kind"] == "tool":
            out.tool_calls += 1
            out.tool_errors += 1 if a.get("is_error") else 0
        elif s["kind"] == "turn":
            out.turns += 1
            out.turn_rows.append(_turn_row(s, children))
        own = s["duration_ms"] - sum(c["duration_ms"] for c in children[s["span_id"]])
        key = s["kind"] if s["kind"] in ("llm", "tool", "wait") else "own"
        out.time_by_kind[key] = out.time_by_kind.get(key, 0) + max(0.0, own)
        for e in s["events"]:
            if e["name"] not in ("first_event", "first_thinking", "first_text"):
                out.milestones.append((e["name"], e["offset_ms"]))
    if not root and spans:  # still running: the gap since the last finished step is unattributed
        out.time_by_kind["own"] = out.time_by_kind.get("own", 0) + max(0.0, total - _covered(spans))
    out.milestones.sort(key=lambda m: m[1])
    return out


def waterfall_rows(spans: list[dict]) -> list[WaterfallRow]:
    """Spans in tree order (each parent followed by its children), for a timeline."""
    children = defaultdict(list)
    ids = {s["span_id"] for s in spans}
    for s in spans:
        parent = s["parent_id"] if s["parent_id"] in ids else None
        children[parent].append(s)
    rows: list[WaterfallRow] = []

    def walk(parent: str | None, depth: int) -> None:
        for s in sorted(children[parent], key=lambda c: c["offset_ms"]):
            rows.append(
                WaterfallRow(
                    s["name"],
                    depth,
                    s["offset_ms"],
                    s["duration_ms"],
                    s["kind"],
                    describe(s),
                    s["status"] == "error",
                )
            )
            walk(s["span_id"], depth + 1)

    walk(None, 0)
    return rows


def describe(s: dict) -> str:
    """A one-line description of a span's most useful attributes."""
    a = s["attributes"]
    if s["kind"] == "llm":
        parts = [a.get("model", "")]
        if a.get("input_tokens") is not None:
            parts.append(f"{a['input_tokens']:,} in / {a.get('output_tokens') or 0:,} out")
        if a.get("thinking_tokens"):
            parts.append(f"{a['thinking_tokens']:,} thinking")
        first = next((e for e in s["events"] if e["name"] == "first_event"), None)
        if first:
            parts.append(f"first token {first['offset_ms'] - s['offset_ms']:,.0f} ms")
        if a.get("stop_reason"):
            parts.append(a["stop_reason"])
        return " · ".join(p for p in parts if p)
    if s["kind"] == "tool":
        return " · ".join(str(x) for x in (a.get("target"), a.get("outcome")) if x)
    if s["status"] == "error":
        return s["error"] or "error"
    shown = {k: v for k, v in a.items() if isinstance(v, (int, float, str)) and len(str(v)) < 60}
    return " · ".join(f"{k.replace('_', ' ')} {v}" for k, v in list(shown.items())[:4])


def _turn_row(turn: dict, children: dict) -> TurnRow:
    row = TurnRow(turn["name"], turn["duration_ms"], [])
    for c in children[turn["span_id"]]:
        a = c["attributes"]
        if c["kind"] == "llm":
            row.input_tokens += a.get("input_tokens") or 0
            row.output_tokens += a.get("output_tokens") or 0
            row.thinking_tokens += a.get("thinking_tokens") or 0
            row.cost_usd += a.get("cost_usd") or 0
            row.stop_reason = a.get("stop_reason")
        elif c["kind"] == "tool":
            row.actions.append(f"{c['name']}({a.get('target', '')})")
    return row


def _end(spans: list[dict]) -> float:
    return max((s["offset_ms"] + s["duration_ms"] for s in spans), default=0.0)


def _covered(spans: list[dict]) -> float:
    """Time covered by the top-level finished spans of a running trace."""
    ids = {s["span_id"] for s in spans}
    return sum(s["duration_ms"] for s in spans if s["parent_id"] not in ids)
