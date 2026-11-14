"""A date timeline: one bar for the whole range, then one bar per item below it.

The axis runs from the earliest start to the latest end, padded on both sides
(5% of the range by default) so bars at the edges stay visible. The overall
bar is drawn in strong ink and the items in grey. Every row also shows its
dates as text, so nothing depends on colour or hovering.

Optionally, each item also gets a 100% bar showing how it breaks down into
categories (for example file types), and a last row shows the breakdown for
everything. Categories keep the same colour on every row and every page, with
a key below listing each category's overall share as text. At most six
categories get colours; fold the rest into one named other_category, drawn
in grey.

With page_size, items are shown that many at a time, with buttons to page
through them. The axis and the key cover every item, so they stay the same
from page to page.
"""

import html
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta

# Bars shorter than this share of the axis (a single day, say) are widened to it.
_MIN_WIDTH_PCT = 0.6
# Categorical colour slots available for breakdowns (--series-1 ... --series-6).
MAX_CATEGORIES = 6


@dataclass(frozen=True)
class Span:
    label: str
    start: date
    end: date
    detail: str = ""  # shown in the tooltip
    breakdown: Sequence[float] = ()  # amount per Timeline.categories, in the same order


@dataclass(frozen=True)
class Timeline:
    title: str
    spans: Sequence[Span]
    overall_label: str = "All"  # label of the bar covering every span
    padding: float = 0.05  # share of the range added before the start and after the end
    subtitle: str = ""
    # Breakdown bars (optional).
    categories: Sequence[str] = ()
    overall_breakdown: Sequence[float] = ()  # the last row's breakdown
    breakdown_label: str = "All"  # label of the last row
    breakdown_unit: str = ""  # e.g. "files", for tooltips
    other_category: str = "Other"  # drawn in grey; does not use a colour slot
    span_heading: str = ""  # column headings, shown when there are breakdowns
    breakdown_heading: str = ""
    # Paging (optional): show this many spans at a time.
    page_size: int | None = None
    item_name: str = "items"  # for the pager: "Folders 1-10 of 23"

    def __post_init__(self):
        colored = [c for c in self.categories if c != self.other_category]
        if len(colored) > MAX_CATEGORIES:
            raise ValueError(
                f"at most {MAX_CATEGORIES} categories get colours; fold the rest into "
                f"{self.other_category!r}"
            )


def timeline_html(t: Timeline) -> str:
    head = f'<div class="timeline{" tl-has-breakdown" if t.categories else ""}"'
    pages = _page_count(t)
    if pages > 1:
        head += f' data-page="1" data-pages="{pages}" data-size="{t.page_size}"'
    head += f'><h3 class="chart-title">{_e(t.title)}</h3>'
    if t.subtitle:
        head += f'<p class="chart-subtitle">{_e(t.subtitle)}</p>'
    if not t.spans:
        return head + "</div>"
    first = min(s.start for s in t.spans)
    last = max(s.end for s in t.spans)
    pad = timedelta(days=max(1, round((last - first).days * t.padding)))
    lo, hi = first - pad, last + pad
    total = (hi - lo).days
    colors = _category_colors(t)

    def pct(d: date) -> float:
        return 100 * (d - lo).days / total

    def track(span: Span | None) -> str:
        if span is None:
            return '<div class="tl-track tl-empty"></div>'
        left = pct(span.start)
        width = min(max(_MIN_WIDTH_PCT, pct(span.end) - left), 100 - left)
        tip = f"{span.label}: {_range(span.start, span.end)}"
        tip += f" ({span.detail})" if span.detail else ""
        return (
            f'<div class="tl-track"><span class="tl-bar" style="left: {left:.2f}%; '
            f'width: {width:.2f}%" title="{_e(tip)}"></span></div>'
        )

    def row(label, span, dates, breakdown, cls, attrs="") -> str:
        cells = [
            f'<div class="tl-label" title="{_e(label)}">{_e(label)}</div>',
            track(span),
            f'<div class="tl-dates">{_e(dates)}</div>',
        ]
        if t.categories:
            cells.append(_breakdown_html(t, label, breakdown, colors))
        return f'<div class="tl-row {cls}"{attrs}>' + "".join(cells) + "</div>"

    rows = []
    if t.categories and (t.span_heading or t.breakdown_heading):
        rows.append(
            '<div class="tl-row tl-head"><div></div>'
            f"<div>{_e(t.span_heading)}</div><div></div>"
            f"<div>{_e(t.breakdown_heading)}</div></div>"
        )
    overall = Span(t.overall_label, first, last)
    rows.append(row(t.overall_label, overall, _range(first, last), None, "tl-overall"))
    for i, s in enumerate(t.spans):
        page = i // t.page_size + 1 if pages > 1 else 1
        attrs = f' data-page="{page}"' + (" hidden" if page > 1 else "")
        rows.append(row(s.label, s, _range(s.start, s.end), s.breakdown, "tl-item", attrs))
    ticks = "".join(
        f'<span class="tl-tick" style="left: {pct(d):.2f}%">{_e(label)}</span>'
        for d, label in _ticks(lo, hi)
    )
    axis = f'<div class="tl-row tl-axis"><div></div><div class="tl-track">{ticks}</div></div>'
    rows.append(axis)
    if t.categories:
        rows.append(row(t.breakdown_label, None, "", t.overall_breakdown, "tl-overall-breakdown"))
    parts = [head, '<div class="tl-rows">', *rows, "</div>"]
    if t.categories:
        parts.append(_key_html(t, colors))
    if pages > 1:
        parts.append(_pager_html(t, pages))
    return "".join(parts) + "</div>"


def _page_count(t: Timeline) -> int:
    if not t.page_size or len(t.spans) <= t.page_size:
        return 1
    return -(-len(t.spans) // t.page_size)


def _category_colors(t: Timeline) -> dict[str, str]:
    colors, slot = {}, 1
    for c in t.categories:
        if c == t.other_category:
            colors[c] = "var(--chart-muted)"
        else:
            colors[c] = f"var(--series-{slot})"
            slot += 1
    return colors


def _shares(values: Sequence[float]) -> list[float]:
    total = sum(values)
    return [100 * v / total if total else 0.0 for v in values]


def _breakdown_html(t: Timeline, label: str, values, colors: dict[str, str]) -> str:
    if not values:
        return '<div class="tl-breakdown tl-empty"></div>'
    segments, summary = [], []
    unit = f" {t.breakdown_unit}" if t.breakdown_unit else ""
    for category, value, share in zip(t.categories, values, _shares(values), strict=True):
        if not value:
            continue
        tip = f"{label}: {category}, {value:,g}{unit} ({share:.0f}%)"
        summary.append(f"{category} {share:.0f}%")
        segments.append(
            f'<span class="tl-seg" style="flex: {share:.3f} 0 0; '
            f'background: {colors[category]}" title="{_e(tip)}"></span>'
        )
    return (
        f'<div class="tl-breakdown" role="img" aria-label="{_e(", ".join(summary))}">'
        + "".join(segments)
        + "</div>"
    )


def _key_html(t: Timeline, colors: dict[str, str]) -> str:
    shares = _shares(t.overall_breakdown) if t.overall_breakdown else [None] * len(t.categories)
    items = "".join(
        f'<span class="legend-item"><span class="swatch" style="background: {colors[c]}">'
        f"</span>{_e(c)}"
        + (f" <strong>{share:.0f}%</strong>" if share is not None else "")
        + "</span>"
        for c, share in zip(t.categories, shares, strict=True)
    )
    return f'<div class="legend tl-key">{items}</div>'


def _pager_html(t: Timeline, pages: int) -> str:
    n = len(t.spans)
    status = f"{t.item_name.capitalize()} 1–{min(t.page_size, n)} of {n:,}"
    return (
        '<div class="tl-pager">'
        '<button type="button" class="tl-prev" disabled>← Previous</button>'
        f'<span class="tl-status" data-name="{_e(t.item_name.capitalize())}" '
        f'data-total="{n}">{_e(status)}</span>'
        '<button type="button" class="tl-next">Next →</button></div>'
    )


def _ticks(lo: date, hi: date) -> list[tuple[date, str]]:
    """Evenly spaced 1 January (or month) marks inside lo..hi, at most about 8."""
    years = (hi - lo).days / 365.25
    if years >= 2:
        step = next((s for s in (1, 2, 5, 10, 20, 50) if years / s <= 8), 100)
        start = (lo.year // step + 1) * step
        return [(date(y, 1, 1), str(y)) for y in range(start, hi.year + 1, step)]
    months = max(1, round(years * 12))
    step = next(s for s in (1, 2, 3, 6, 12) if months / s <= 8)
    marks = []
    y, m = lo.year, lo.month
    while True:
        m += 1
        if m > 12:
            y, m = y + 1, 1
        d = date(y, m, 1)
        if d > hi:
            return marks
        if (m - 1) % step == 0:
            marks.append((d, d.strftime("%b %Y") if m == 1 or not marks else d.strftime("%b")))


def _range(start: date, end: date) -> str:
    return start.isoformat() if start == end else f"{start.isoformat()} \u2013 {end.isoformat()}"


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


# Paging works by event delegation, so it also works in a live app that swaps
# the page body in without running scripts.
TIMELINE_JS = """
document.addEventListener('click', (event) => {
  const button = event.target.closest('.tl-pager button');
  if (!button) return;
  const tl = button.closest('.timeline');
  const pages = Number(tl.dataset.pages), size = Number(tl.dataset.size);
  const step = button.classList.contains('tl-next') ? 1 : -1;
  const page = Math.min(pages, Math.max(1, Number(tl.dataset.page) + step));
  tl.dataset.page = page;
  for (const row of tl.querySelectorAll('.tl-item')) {
    row.hidden = Number(row.dataset.page) !== page;
  }
  tl.querySelector('.tl-prev').disabled = page === 1;
  tl.querySelector('.tl-next').disabled = page === pages;
  const status = tl.querySelector('.tl-status');
  const total = Number(status.dataset.total);
  status.textContent = `${status.dataset.name} ${(page - 1) * size + 1}–` +
    `${Math.min(page * size, total)} of ${total.toLocaleString()}`;
});
"""

TIMELINE_CSS = """
.timeline { background: var(--chart-surface); border-radius: 10px; padding: 12px;
  margin: 0 0 16px; }
.tl-rows { margin-top: 10px; display: grid; gap: 3px; }
.tl-row { display: grid; grid-template-columns: minmax(120px, 24%) 1fr minmax(96px, 20%);
  column-gap: 10px; align-items: center; font-size: 13px; }
.tl-has-breakdown .tl-row {
  grid-template-columns: minmax(80px, 16%) 1fr minmax(168px, auto) minmax(110px, 22%); }
.tl-row[hidden] { display: none; }
.tl-head { color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: .04em; }
.tl-label { white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tl-track { position: relative; height: 16px; border-bottom: 1px solid var(--chart-grid); }
.tl-track.tl-empty { border-bottom: none; }
.tl-bar { position: absolute; top: 3px; height: 10px; border-radius: 4px;
  background: var(--muted); }
.tl-overall .tl-bar { background: var(--text); top: 2px; height: 12px; }
.tl-overall, .tl-overall-breakdown { font-weight: 600; }
.tl-overall { padding-bottom: 4px; }
.tl-overall-breakdown { padding-top: 4px; }
.tl-dates { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.tl-breakdown { display: flex; gap: 2px; height: 12px; }
.tl-breakdown.tl-empty { height: 0; }
.tl-seg { min-width: 2px; }
.tl-seg:first-child { border-radius: 4px 0 0 4px; }
.tl-seg:last-child { border-radius: 0 4px 4px 0; }
.tl-seg:only-child { border-radius: 4px; }
.tl-axis .tl-track { border-bottom: none; height: 18px; }
.tl-tick { position: absolute; top: 2px; transform: translateX(-50%); color: var(--muted);
  font-size: 11px; white-space: nowrap; }
.tl-tick::before { content: ""; position: absolute; left: 50%; top: -4px; height: 4px;
  border-left: 1px solid var(--chart-axis); }
.tl-key { margin-top: 10px; }
.tl-pager { display: flex; align-items: center; justify-content: center; gap: 12px;
  margin-top: 10px; font-size: 13px; }
.tl-pager button { font: inherit; padding: 4px 10px; border-radius: 6px; cursor: pointer;
  border: 1px solid var(--border); background: var(--surface); color: var(--text); }
.tl-pager button:disabled { opacity: .4; cursor: default; }
.tl-status { color: var(--muted); font-variant-numeric: tabular-nums; }
@media (max-width: 560px) {
  .tl-row, .tl-has-breakdown .tl-row { grid-template-columns: 1fr auto; }
  .tl-track { grid-column: 1 / -1; grid-row: 2; }
  .tl-breakdown { grid-column: 1 / -1; grid-row: 3; } }
"""
