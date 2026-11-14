"""Pages and page parts for traces, built with botview."""

from datetime import datetime
from statistics import median

from bottrace.summary import KIND_LABELS, TraceSummary, summarize, waterfall_rows
from botview import Badge, Block, Page, Section, Step, Table, Text, Waterfall

MILESTONE_LABELS = {
    "first_result_shown": "First result on screen",
    "analysis_done": "Analysis finished",
}


def timing_blocks(spans: list[dict], now_ms: float | None = None) -> list[Block]:
    """A trace's timeline, where its time went, and its agent turns.
    now_ms: for a trace still running, the time since it started."""
    s = summarize(spans, now_ms)
    steps = [
        Step(
            r.label,
            r.depth,
            r.start_ms,
            r.duration_ms,
            r.kind if r.kind in ("llm", "tool", "wait") else "stage",
            r.detail,
            r.error,
        )
        for r in waterfall_rows(spans)
    ]
    blocks: list[Block] = [
        Waterfall(
            "Timeline",
            steps,
            total_ms=s.total_ms,
            subtitle=f"{_seconds(s.total_ms)} from request to answer.",
        ),
        Table(
            ["Where the time went", "Time", "Share"],
            [
                [
                    KIND_LABELS[k],
                    _seconds(ms),
                    f"{100 * ms / s.total_ms:.0f}%" if s.total_ms else "",
                ]
                for k, ms in sorted(s.time_by_kind.items(), key=lambda kv: -kv[1])
            ],
            numeric=frozenset({1, 2}),
        ),
    ]
    notes = [
        f"{MILESTONE_LABELS.get(n, n.replace('_', ' '))}: {_seconds(ms)}" for n, ms in s.milestones
    ]
    if s.first_token_ms:
        notes.append(
            f"Time to first token: {_seconds(median(s.first_token_ms))} "
            f"(median of {len(s.first_token_ms)} model call(s))"
        )
    if notes:
        blocks.append(Text(" · ".join(notes)))
    if s.turn_rows:
        blocks.append(
            Table(
                [
                    "Agent turn",
                    "Time",
                    "Actions",
                    "Input tokens",
                    "Output tokens",
                    "Thinking",
                    "Est. cost",
                    "Ended with",
                ],
                [
                    [
                        t.name,
                        _seconds(t.duration_ms),
                        ", ".join(t.actions) or "answer",
                        f"{t.input_tokens:,}",
                        f"{t.output_tokens:,}",
                        f"{t.thinking_tokens:,}",
                        _usd(t.cost_usd),
                        t.stop_reason or "",
                    ]
                    for t in s.turn_rows
                ],
                numeric=frozenset({1, 3, 4, 5, 6}),
            )
        )
    if s.errors:
        blocks.append(Text("; ".join(s.errors), tone="warning", label="Errors"))
    return blocks


def traces_page(traces: list[list[dict]], detail: int = 5) -> Page:
    """Recent traces, newest first: a table of all, and timelines for the newest few."""
    return Page(
        title="BoringBots traces",
        subtitle="What each request did, how long each step took, and what it cost.",
        facts=traces_facts(traces),
        sections=traces_sections(traces, detail),
    )


def traces_facts(traces: list[list[dict]]) -> list[tuple[str, str]]:
    """Headline figures for a set of traces."""
    summaries = [summarize(t) for t in traces]
    totals = [s.total_ms for s in summaries]
    return [
        ("Requests shown", f"{len(summaries)}"),
        ("Median time to answer", _seconds(median(totals)) if totals else "-"),
        ("Slowest", _seconds(max(totals)) if totals else "-"),
        ("Model calls", f"{sum(s.llm_calls for s in summaries):,}"),
        ("Estimated cost", _usd(sum(s.cost_usd for s in summaries))),
    ]


def traces_sections(traces: list[list[dict]], detail: int = 5) -> list[Section]:
    """The traces page's content: a table of all, and timelines for the newest few.
    Also used to show a traces snapshot inside a bot's own page."""
    summaries = [summarize(t) for t in traces]
    if not summaries:
        return [Section("No traces yet", [], [Text("Run a bot, then come back.")])]
    sections = [_overview(summaries)]
    for spans, s in list(zip(traces, summaries, strict=True))[:detail]:
        badges = [Badge(_seconds(s.total_ms), "info")]
        if s.status != "ok":
            badges.append(Badge(s.status, "warning"))
        sections.append(
            Section(f"{s.bot}: {s.name}, {_when(s.started_at)}", badges, timing_blocks(spans))
        )
    return sections


def _overview(summaries: list[TraceSummary]) -> Section:
    rows, tones = [], []
    for s in summaries:
        rows.append(
            [
                _when(s.started_at),
                s.bot,
                s.name,
                _seconds(s.total_ms),
                f"{s.llm_calls}",
                f"{s.turns}",
                f"{s.tool_calls}",
                f"{s.input_tokens:,} / {s.output_tokens:,} ({s.thinking_tokens:,})",
                _usd(s.cost_usd),
                s.status,
            ]
        )
        tones.append("warning" if s.status != "ok" else None)
    return Section(
        "Recent requests",
        [Badge("newest first")],
        [
            Table(
                [
                    "When",
                    "Bot",
                    "Request",
                    "Time",
                    "Model calls",
                    "Turns",
                    "Tool calls",
                    "Tokens in / out (thinking)",
                    "Est. cost",
                    "Status",
                ],
                rows,
                row_tones=tones,
                numeric=frozenset({3, 4, 5, 6, 8}),
            )
        ],
    )


def _seconds(ms: float) -> str:
    return f"{ms / 1000:,.1f} s" if ms >= 1000 else f"{ms:,.0f} ms"


def _usd(amount: float) -> str:
    if amount == 0:
        return "$0.00"
    return f"${amount:.4f}" if amount < 0.01 else f"${amount:,.3f}"


def _when(iso: str) -> str:
    try:
        return f"{datetime.fromisoformat(iso).astimezone():%b %-d, %H:%M:%S}"
    except ValueError:
        return iso
