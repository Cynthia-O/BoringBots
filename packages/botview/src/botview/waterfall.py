"""A waterfall timeline: one row per step, each bar placed where it ran.

Used for traces: how long a request took and what each step contributed.
Bars are coloured by kind of work, with a legend; every row also shows its
duration and details as text, so nothing depends on colour or hovering.
"""

import html
from collections.abc import Sequence
from dataclasses import dataclass

# Kinds of work, in the fixed order their colours are assigned.
KIND_ORDER = ("stage", "llm", "tool")
KIND_NAMES = {"stage": "Bot step", "llm": "Model call", "tool": "Tool call"}
# Time spent waiting on the person is drawn in neutral grey, apart from the bot's work.


@dataclass(frozen=True)
class Step:
    label: str
    depth: int  # 0 for the request itself, 1 for its steps, and so on
    start_ms: float
    duration_ms: float
    kind: str  # "llm", "tool", or anything else for the bot's own steps
    detail: str = ""
    error: bool = False


@dataclass(frozen=True)
class Waterfall:
    title: str
    steps: Sequence[Step]
    total_ms: float | None = None  # the timeline's length; defaults to the last step's end
    subtitle: str = ""


def waterfall_html(w: Waterfall) -> str:
    total = w.total_ms or max((s.start_ms + s.duration_ms for s in w.steps), default=1.0)
    total = max(total, 1e-6)
    head = f'<div class="waterfall"><h3 class="chart-title">{_e(w.title)}</h3>'
    if w.subtitle:
        head += f'<p class="chart-subtitle">{_e(w.subtitle)}</p>'
    head += (
        '<div class="legend">'
        + "".join(
            f'<span class="legend-item"><span class="swatch" style="background: var(--series-{i})">'
            f"</span>{KIND_NAMES[k]}</span>"
            for i, k in enumerate(KIND_ORDER, 1)
        )
        + "</div>"
    )
    rows = []
    for s in w.steps:
        left = 100 * s.start_ms / total
        width = max(0.4, 100 * s.duration_ms / total)
        slot = KIND_ORDER.index(s.kind) + 1 if s.kind in KIND_ORDER else 1
        color = "var(--chart-axis)" if s.kind == "wait" else f"var(--series-{slot})"
        cls = " wf-error" if s.error else ""
        rows.append(
            f'<div class="wf-row{cls}">'
            f'<div class="wf-label" style="padding-left: {s.depth * 14}px" '
            f'title="{_e(s.label)}">{_e(s.label)}</div>'
            f'<div class="wf-track"><span class="wf-bar" style="left: {left:.2f}%; '
            f'width: {min(width, 100 - left):.2f}%; background: {color}"></span></div>'
            f'<div class="wf-time">{_ms(s.duration_ms)}</div>'
            f'<div class="wf-detail">{_e(s.detail)}</div></div>'
        )
    return head + '<div class="wf-rows">' + "".join(rows) + "</div></div>"


def _ms(ms: float) -> str:
    return f"{ms / 1000:,.2f} s" if ms >= 1000 else f"{ms:,.0f} ms"


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


WATERFALL_CSS = """
.waterfall { background: var(--chart-surface); border-radius: 10px; padding: 12px;
  margin: 0 0 16px; }
.wf-rows { margin-top: 10px; display: grid; gap: 2px; }
.wf-row { display: grid; grid-template-columns: minmax(120px, 26%) 1fr 72px; column-gap: 10px;
  align-items: center; font-size: 13px; }
.wf-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wf-track { position: relative; height: 14px; border-bottom: 1px solid var(--chart-grid); }
.wf-bar { position: absolute; top: 2px; height: 10px; border-radius: 3px; }
.wf-time { text-align: right; font-variant-numeric: tabular-nums; color: var(--muted); }
.wf-detail { grid-column: 1 / -1; color: var(--muted); font-size: 12px; padding: 0 0 4px;
  overflow-wrap: anywhere; }
.wf-detail:empty { display: none; }
.wf-error .wf-label, .wf-error .wf-detail { color: var(--warning-fg); }
@media (max-width: 560px) { .wf-row { grid-template-columns: 1fr 64px; }
  .wf-track { grid-column: 1 / -1; grid-row: 2; } }
"""
