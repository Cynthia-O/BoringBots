"""Charts as inline SVG: columns (single or stacked) and a single line.

No chart library and nothing loaded from the web, in keeping with botview's
self-contained pages. Follows a few fixed rules: one y-axis per chart, thin
marks (columns at most 24px wide with a 4px rounded top, 2px lines), a 2px gap
between stacked segments, hairline gridlines, colors assigned to series in a
fixed order from a palette validated for color-vision deficiency, a legend for
two or more series, and a hover/focus tooltip on every chart. Tooltips never
hold the only copy of a value: pair each chart with a table of the same numbers.
"""

import html
import json
import math
from collections.abc import Sequence
from dataclasses import dataclass

# Series colors, in this fixed order (CSS variables, light and dark in page.CSS).
MAX_SERIES = 3
CHART_WIDTH, PLOT_HEIGHT = 720, 200
MARGIN_LEFT, MARGIN_RIGHT, MARGIN_TOP, AXIS_BAND = 64, 72, 12, 28
MAX_BAR_WIDTH, BAR_RADIUS, SEGMENT_GAP = 24, 4, 2
MAX_X_LABELS = 8


@dataclass(frozen=True)
class Series:
    name: str
    values: Sequence[float]


@dataclass(frozen=True)
class Chart:
    title: str
    kind: str  # "columns" (stacked when several series) or "line" (one series)
    x_labels: Sequence[str]
    series: Sequence[Series]
    unit: str = "number"  # "number" or "usd": how values and ticks are written
    subtitle: str = ""

    def __post_init__(self):
        if self.kind not in ("columns", "line"):
            raise ValueError(f"kind must be columns or line, got {self.kind!r}")
        if not 1 <= len(self.series) <= MAX_SERIES:
            raise ValueError(f"a chart takes 1 to {MAX_SERIES} series")
        if self.kind == "line" and len(self.series) != 1:
            raise ValueError("a line chart takes one series")
        if any(len(s.values) != len(self.x_labels) for s in self.series):
            raise ValueError("every series needs one value per x label")


def chart_html(chart: Chart) -> str:
    head = f'<div class="chart"><h3 class="chart-title">{_e(chart.title)}</h3>'
    if chart.subtitle:
        head += f'<p class="chart-subtitle">{_e(chart.subtitle)}</p>'
    if len(chart.series) > 1:
        items = "".join(
            f'<span class="legend-item"><span class="swatch" '
            f'style="background: var(--series-{i})"></span>{_e(s.name)}</span>'
            for i, s in enumerate(chart.series, 1)
        )
        head += f'<div class="legend">{items}</div>'
    if not chart.x_labels:
        return head + '<p class="chart-subtitle">No data yet.</p></div>'
    return head + _svg(chart) + "</div>"


def _svg(chart: Chart) -> str:
    n = len(chart.x_labels)
    plot_w = CHART_WIDTH - MARGIN_LEFT - MARGIN_RIGHT
    top, bottom = MARGIN_TOP, MARGIN_TOP + PLOT_HEIGHT
    totals = [sum(s.values[i] for s in chart.series) for i in range(n)]
    ticks = _ticks(max(totals) if chart.kind == "columns" else max(chart.series[0].values))
    y_max = ticks[-1] or 1
    band = plot_w / n

    def y(v: float) -> float:
        return bottom - (v / y_max) * PLOT_HEIGHT

    def x_center(i: int) -> float:
        return MARGIN_LEFT + band * (i + 0.5)

    parts = [
        f'<svg class="chart-svg" viewBox="0 0 {CHART_WIDTH} {bottom + AXIS_BAND}" '
        f'role="img" aria-label="{_e(chart.title)}">'
    ]
    for t in ticks:  # hairline grid and y ticks
        ty = y(t)
        parts.append(
            f'<line class="grid" x1="{MARGIN_LEFT}" x2="{MARGIN_LEFT + plot_w}" '
            f'y1="{ty:.1f}" y2="{ty:.1f}"/>'
            f'<text class="tick" x="{MARGIN_LEFT - 8}" y="{ty + 4:.1f}" text-anchor="end">'
            f"{_e(_format(t, chart.unit, tick=True, step=ticks[1] - ticks[0]))}</text>"
        )
    parts.append(
        f'<line class="baseline" x1="{MARGIN_LEFT}" x2="{MARGIN_LEFT + plot_w}" '
        f'y1="{bottom}" y2="{bottom}"/>'
    )
    every = max(1, math.ceil(n / MAX_X_LABELS))
    for i, label in enumerate(chart.x_labels):  # x labels, thinned so they never collide
        if i % every == 0 or i == n - 1 and (n - 1) % every > every / 2:
            parts.append(
                f'<text class="tick" x="{x_center(i):.1f}" y="{bottom + 18}" '
                f'text-anchor="middle">{_e(label)}</text>'
            )

    if chart.kind == "columns":
        bar_w = min(MAX_BAR_WIDTH, band * 0.6)
        for i in range(n):
            x0 = x_center(i) - bar_w / 2
            base = bottom
            stack = [(k, s.values[i]) for k, s in enumerate(chart.series, 1) if s.values[i] > 0]
            parts.append(f'<g class="mark" data-i="{i}">')
            for j, (k, v) in enumerate(stack):
                h = (v / y_max) * PLOT_HEIGHT
                gap = SEGMENT_GAP if j > 0 else 0  # surface gap between stacked segments
                seg_top, seg_bottom = base - h, base - gap
                if seg_bottom - seg_top < 0.5:
                    base -= h
                    continue
                rounded = j == len(stack) - 1
                parts.append(
                    f'<path fill="var(--series-{k})" d="'
                    f'{_column_path(x0, seg_top, bar_w, seg_bottom - seg_top, rounded)}"/>'
                )
                base -= h
            parts.append("</g>")
    else:
        values = chart.series[0].values
        points = [(x_center(i), y(v)) for i, v in enumerate(values)]
        line = " ".join(f"{px:.1f},{py:.1f}" for px, py in points)
        area = f"{points[0][0]:.1f},{bottom} {line} {points[-1][0]:.1f},{bottom}"
        parts.append(f'<polygon class="area" points="{area}"/>')
        parts.append(f'<polyline class="line" points="{line}"/>')
        ex, ey = points[-1]
        parts.append(f'<circle class="end-dot" cx="{ex:.1f}" cy="{ey:.1f}" r="4"/>')
        parts.append(  # direct label at the end only, never on every point
            f'<text class="end-label" x="{ex + 10:.1f}" y="{ey + 4:.1f}">'
            f"{_e(_format(values[-1], chart.unit))}</text>"
        )
        parts.append(f'<line class="crosshair" y1="{top}" y2="{bottom}" x1="0" x2="0"/>')

    for i, label in enumerate(chart.x_labels):  # hit areas: the whole band, bigger than the mark
        rows = [
            [s.name, _format(s.values[i], chart.unit), f"var(--series-{k})"]
            for k, s in enumerate(chart.series, 1)
        ]
        if chart.kind == "columns" and len(chart.series) > 1:
            rows.append(["Total", _format(totals[i], chart.unit), ""])
        tip = _e(json.dumps({"x": label, "rows": rows, "cx": round(x_center(i), 1)}))
        summary = _e(f"{label}: " + ", ".join(f"{r[0]} {r[1]}" for r in rows))
        parts.append(
            f'<rect class="chart-hit" tabindex="0" data-i="{i}" data-tip="{tip}" '
            f'aria-label="{summary}" x="{MARGIN_LEFT + band * i:.1f}" y="{top}" '
            f'width="{band:.1f}" height="{PLOT_HEIGHT}"/>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _column_path(x: float, top: float, w: float, h: float, rounded: bool) -> str:
    """A column with a rounded top (data end) and a square bottom (baseline)."""
    r = min(BAR_RADIUS, w / 2, h) if rounded else 0
    return (
        f"M{x:.1f},{top + h:.1f} L{x:.1f},{top + r:.1f} "
        f"Q{x:.1f},{top:.1f} {x + r:.1f},{top:.1f} L{x + w - r:.1f},{top:.1f} "
        f"Q{x + w:.1f},{top:.1f} {x + w:.1f},{top + r:.1f} L{x + w:.1f},{top + h:.1f} Z"
    )


def _ticks(maximum: float, count: int = 4) -> list[float]:
    """Clean tick values from 0 to at least maximum (steps of 1, 2 or 5 x 10^k)."""
    if maximum <= 0:
        return [0.0, 1.0]
    raw = maximum / count
    power = 10 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1, 2, 5, 10) if m * power >= raw)
    top = math.ceil(maximum / step - 1e-9) * step
    return [round(i * step, 10) for i in range(int(round(top / step)) + 1)]


def _format(value: float, unit: str, tick: bool = False, step: float = 0) -> str:
    if unit == "usd":
        if tick:
            decimals = 0 if step >= 1 else max(2, -math.floor(math.log10(step)) if step else 2)
            return f"${value:,.{decimals}f}"
        if value == 0:
            return "$0.00"
        if value < 0.01:
            return f"${value:.4f}"
        return f"${value:,.2f}" if value >= 1 else f"${value:.3f}"
    if tick and value >= 10_000:
        for size, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
            if value >= size:
                return f"{value / size:g}{suffix}"
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.2f}"


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


CHART_CSS = """
.chart { background: var(--chart-surface); border-radius: 10px; padding: 12px 12px 4px;
  margin: 0 0 16px; position: relative; }
.chart-title { font-size: 15px; margin: 0; }
.chart-subtitle { color: var(--muted); font-size: 13px; margin: 2px 0 0; }
.legend { display: flex; flex-wrap: wrap; gap: 4px 16px; margin: 8px 0 0; font-size: 13px;
  color: var(--muted); }
.legend-item { display: inline-flex; align-items: center; gap: 6px; }
.swatch { width: 10px; height: 10px; border-radius: 2px; display: inline-block; }
.chart-svg { width: 100%; height: auto; display: block; margin-top: 8px; overflow: visible; }
.chart-svg .grid { stroke: var(--chart-grid); stroke-width: 1; }
.chart-svg .baseline { stroke: var(--chart-axis); stroke-width: 1; }
.chart-svg .tick { fill: var(--chart-muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.chart-svg .line { fill: none; stroke: var(--series-1); stroke-width: 2; stroke-linejoin: round;
  stroke-linecap: round; }
.chart-svg .area { fill: var(--series-1); opacity: .1; }
.chart-svg .end-dot { fill: var(--series-1); stroke: var(--chart-surface); stroke-width: 2; }
.chart-svg .end-label { fill: var(--text); font-size: 12px; font-weight: 600; }
.chart-svg .crosshair { stroke: var(--chart-axis); stroke-width: 1; visibility: hidden; }
.chart-svg .chart-hit { fill: transparent; cursor: default; outline: none; }
.chart-svg .mark.hover path { filter: brightness(1.12); }
.chart-tip { position: absolute; pointer-events: none; background: var(--surface);
  border: 1px solid var(--border); border-radius: 8px; padding: 6px 10px; font-size: 12px;
  box-shadow: 0 2px 8px rgba(0,0,0,.12); z-index: 5; white-space: nowrap; }
.chart-tip .tip-x { color: var(--muted); margin-bottom: 2px; }
.chart-tip .tip-row { display: flex; align-items: center; gap: 6px; }
.chart-tip .tip-key { width: 12px; height: 2px; border-radius: 1px; display: inline-block; }
.chart-tip strong { font-variant-numeric: tabular-nums; }
.chart-tip .tip-name { color: var(--muted); }
"""

CHART_JS = """
(() => {
  function show(hit) {
    const chart = hit.closest('.chart');
    const svg = hit.closest('svg');
    const data = JSON.parse(hit.dataset.tip);
    let tip = chart.querySelector('.chart-tip');
    if (!tip) {
      tip = document.createElement('div');
      tip.className = 'chart-tip';
      chart.append(tip);
    }
    tip.replaceChildren();
    const x = document.createElement('div'); x.className = 'tip-x'; x.textContent = data.x;
    tip.append(x);
    for (const [name, value, color] of data.rows) {
      const row = document.createElement('div'); row.className = 'tip-row';
      if (color) {
        const key = document.createElement('span'); key.className = 'tip-key';
        key.style.background = color; row.append(key);
      }
      const v = document.createElement('strong'); v.textContent = value;
      const n = document.createElement('span'); n.className = 'tip-name'; n.textContent = name;
      row.append(v, n); tip.append(row);
    }
    for (const m of svg.querySelectorAll('.mark')) {
      m.classList.toggle('hover', m.dataset.i === hit.dataset.i);
    }
    const cross = svg.querySelector('.crosshair');
    if (cross) {
      cross.setAttribute('x1', data.cx);
      cross.setAttribute('x2', data.cx);
      cross.style.visibility = 'visible';
    }
    const box = svg.getBoundingClientRect(), host = chart.getBoundingClientRect();
    const scale = box.width / svg.viewBox.baseVal.width;
    let left = box.left - host.left + data.cx * scale + 12;
    if (left + tip.offsetWidth > host.width) left -= tip.offsetWidth + 24;
    tip.style.left = `${Math.max(0, left)}px`;
    tip.style.top = `${box.top - host.top + 8}px`;
    tip.hidden = false;
  }
  function hide(hit) {
    const chart = hit.closest('.chart');
    const tip = chart.querySelector('.chart-tip'); if (tip) tip.hidden = true;
    const cross = chart.querySelector('.crosshair'); if (cross) cross.style.visibility = 'hidden';
    for (const m of chart.querySelectorAll('.mark.hover')) m.classList.remove('hover');
  }
  const hitOf = (e) => e.target.closest && e.target.closest('.chart-hit');
  for (const type of ['pointerover', 'focusin']) {
    document.addEventListener(type, (e) => { const h = hitOf(e); if (h) show(h); });
  }
  for (const type of ['pointerout', 'focusout']) {
    document.addEventListener(type, (e) => { const h = hitOf(e); if (h) hide(h); });
  }
})();
"""
