"""Describe a page as data, then render it to one self-contained HTML file.

Bots build a Page out of a few generic blocks (text, tables, cards with badges)
and never write HTML themselves, so every bot's pages look and behave alike.
All styling and script are inline and nothing is loaded from the web, so a
page works offline and the file names on it never leave the machine.
"""

import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from botview.charts import CHART_CSS, CHART_JS, Chart, chart_html

# Badge and row tones. Each has a colour in light and dark themes.
TONES = ("neutral", "good", "info", "caution", "warning")


@dataclass(frozen=True)
class Badge:
    label: str
    tone: str = "neutral"


@dataclass(frozen=True)
class Text:
    text: str
    tone: str | None = None  # None for plain text; a tone draws it as a callout
    label: str | None = None  # bold lead-in, e.g. "Check before deleting"
    fine_print: bool = False  # small, muted text for caveats


@dataclass(frozen=True)
class Action:
    """A button in a table cell that sends action (with data) to a live app.
    Hidden on saved pages, which have no app to send it to."""

    label: str
    action: str
    data: Mapping[str, object] = field(default_factory=dict)
    title: str = ""  # tooltip


@dataclass(frozen=True)
class Table:
    columns: Sequence[str]
    rows: Sequence[Sequence[object]]
    # Optional tone per row (None for plain), to pick out rows such as
    # suggested deletions. Same length as rows when given.
    row_tones: Sequence[str | None] = ()
    # Column indexes that hold numbers, drawn right-aligned.
    numeric: frozenset[int] = frozenset()


@dataclass(frozen=True)
class Card:
    title: str
    badges: Sequence[Badge] = ()
    blocks: Sequence["Block"] = ()


Block = Text | Table | Card | Chart


@dataclass(frozen=True)
class Section:
    title: str
    badges: Sequence[Badge] = ()
    blocks: Sequence[Block] = ()


@dataclass(frozen=True)
class Page:
    title: str
    subtitle: str | None = None
    facts: Sequence[tuple[str, str]] = ()  # label/value pairs shown under the title
    notices: Sequence[Text] = ()  # page-wide callouts, e.g. warnings
    sections: Sequence[Section] = field(default_factory=tuple)
    searchable: bool = True  # show a box that filters sections by their text
    hero: tuple[str, str] | None = None  # (label, value): the one number the page leads with


def render(page: Page) -> str:
    """The page as a complete HTML document."""
    return document(page.title, render_body(page))


def document(title: str, body: str, script: str = "", body_class: str = "") -> str:
    """A complete HTML document around body, with the shared styles and the filter script."""
    return "\n".join(
        [
            "<!doctype html>",
            '<html lang="en"><head><meta charset="utf-8">',
            '<meta name="viewport" content="width=device-width, initial-scale=1">',
            f"<title>{_e(title)}</title>",
            f'<style>{CSS}</style></head><body class="{_e(body_class)}"><main>',
            body,
            "</main>",
            f"<script>{FILTER_JS}{CHART_JS}{script}</script>",
            "</body></html>",
        ]
    )


def render_body(page: Page) -> str:
    """The page's content without the document around it, for showing inside
    another page (such as a live botview app)."""
    parts = ['<div class="page-body">', f"<header><h1>{_e(page.title)}</h1>"]
    if page.subtitle:
        parts.append(f'<p class="subtitle">{_e(page.subtitle)}</p>')
    if page.hero:
        label, value = page.hero
        parts.append(
            f'<div class="hero"><div class="hero-label">{_e(label)}</div>'
            f'<div class="hero-value">{_e(value)}</div></div>'
        )
    if page.facts:
        parts.append(facts_html(page.facts))
    parts.append("</header>")
    parts.extend(_text(n) for n in page.notices)
    if page.searchable and page.sections:
        parts.append(
            '<input class="filter" type="search" placeholder="Filter by any text on the page" '
            'aria-label="Filter sections">'
            '<p class="no-match" hidden>Nothing matches.</p>'
        )
    parts.extend(section_html(s) for s in page.sections)
    parts.append("</div>")
    return "\n".join(parts)


def facts_html(facts: Sequence[tuple[str, str]]) -> str:
    items = "".join(f"<div><dt>{_e(k)}</dt><dd>{_e(v)}</dd></div>" for k, v in facts)
    return f'<dl class="facts">{items}</dl>'


def section_html(section: Section) -> str:
    return _section(section)


def _section(section: Section) -> str:
    out = [f"<section><h2>{_e(section.title)}{_badges(section.badges)}</h2>"]
    out.extend(_block(b) for b in section.blocks)
    out.append("</section>")
    return "\n".join(out)


def _block(block: Block) -> str:
    if isinstance(block, Text):
        return _text(block)
    if isinstance(block, Table):
        return _table(block)
    if isinstance(block, Chart):
        return chart_html(block)
    if isinstance(block, Card):
        inner = "\n".join(_block(b) for b in block.blocks)
        return f'<div class="card"><h3>{_e(block.title)}{_badges(block.badges)}</h3>{inner}</div>'
    raise TypeError(f"not a page block: {block!r}")


def _text(text: Text) -> str:
    label = f"<strong>{_e(text.label)}:</strong> " if text.label else ""
    if text.tone:
        return f'<p class="callout tone-{_tone(text.tone)}">{label}{_e(text.text)}</p>'
    if text.fine_print:
        return f'<p class="fine-print">{label}{_e(text.text)}</p>'
    return f"<p>{label}{_e(text.text)}</p>"


def _table(table: Table) -> str:
    if table.row_tones and len(table.row_tones) != len(table.rows):
        raise ValueError("row_tones must have one entry per row")

    def cell(tag: str, i: int, value: object) -> str:
        if isinstance(value, Action):
            return f'<{tag} class="action-cell">{_action(value)}</{tag}>'

        cls = ' class="num"' if i in table.numeric else ""
        return f"<{tag}{cls}>{_e('' if value is None else str(value))}</{tag}>"

    head = "".join(cell("th", i, c) for i, c in enumerate(table.columns))
    rows = []
    for r, row in enumerate(table.rows):
        tone = table.row_tones[r] if table.row_tones else None
        cls = f' class="row-{_tone(tone)}"' if tone else ""
        rows.append(f"<tr{cls}>" + "".join(cell("td", i, v) for i, v in enumerate(row)) + "</tr>")
    return (
        f'<div class="table-wrap"><table><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>"
    )


def _action(action: Action) -> str:
    payload = _e(json.dumps(dict(action.data)))
    return (
        f'<button type="button" class="cell-action" data-action="{_e(action.action)}" '
        f'data-payload="{payload}" title="{_e(action.title)}">{_e(action.label)}</button>'
    )


def _badges(badges: Sequence[Badge]) -> str:
    return "".join(
        f' <span class="badge tone-{_tone(b.tone)}">{_e(b.label)}</span>' for b in badges
    )


def _tone(tone: str) -> str:
    if tone not in TONES:
        raise ValueError(f"tone must be one of {', '.join(TONES)}, got {tone!r}")
    return tone


def _e(text: str) -> str:
    return html.escape(text, quote=True)


CSS = (
    """
:root {
  --bg: #f7f7f5; --surface: #ffffff; --text: #1d1d1f; --muted: #6b6b70; --border: #e2e2e0;
  --neutral-bg: #ececea; --neutral-fg: #3a3a3c;
  --good-bg: #e3f4e8; --good-fg: #1f6b3a;
  --info-bg: #e5eefb; --info-fg: #1f4f8f;
  --caution-bg: #fdf1d8; --caution-fg: #7a5200;
  --warning-bg: #fbe4e1; --warning-fg: #9b2c1f;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --bg: #161618; --surface: #1f1f22; --text: #ededf0; --muted: #a0a0a8; --border: #34343a;
    --neutral-bg: #2c2c30; --neutral-fg: #d6d6dc;
    --good-bg: #173322; --good-fg: #8fd8a8;
    --info-bg: #172a45; --info-fg: #9cc2f5;
    --caution-bg: #3a2c0c; --caution-fg: #f2cd7a;
    --warning-bg: #3d1a16; --warning-fg: #f4a99e;
  }
}
:root[data-theme="dark"] {
  --bg: #161618; --surface: #1f1f22; --text: #ededf0; --muted: #a0a0a8; --border: #34343a;
  --neutral-bg: #2c2c30; --neutral-fg: #d6d6dc;
  --good-bg: #173322; --good-fg: #8fd8a8;
  --info-bg: #172a45; --info-fg: #9cc2f5;
  --caution-bg: #3a2c0c; --caution-fg: #f2cd7a;
  --warning-bg: #3d1a16; --warning-fg: #f4a99e;
}
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--text);
  font: 15px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 32px 16px 64px; }
h1 { font-size: 26px; margin: 0 0 4px; }
h2 { font-size: 18px; margin: 0 0 12px; display: flex; flex-wrap: wrap; gap: 6px;
  align-items: center; }
h3 { font-size: 15px; margin: 0 0 8px; display: flex; flex-wrap: wrap; gap: 6px;
  align-items: center; }
.subtitle { color: var(--muted); margin: 0 0 16px; }
.facts { display: flex; flex-wrap: wrap; gap: 8px 24px; margin: 0 0 24px; }
.facts dt { color: var(--muted); font-size: 12px; text-transform: uppercase;
  letter-spacing: .04em; }
.facts dd { margin: 0; font-weight: 600; }
section { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; margin: 0 0 16px; }
.card { border-top: 1px solid var(--border); padding-top: 12px; margin-top: 12px; }
section > .card:first-of-type { border-top: 0; padding-top: 0; margin-top: 0; }
p { margin: 0 0 8px; }
.callout { padding: 8px 12px; border-radius: 8px; }
.fine-print { font-size: 12px; color: var(--muted); margin-top: 12px; }
.badge { font-size: 12px; font-weight: 600; padding: 2px 8px; border-radius: 999px;
  white-space: nowrap; }
.tone-neutral { background: var(--neutral-bg); color: var(--neutral-fg); }
.tone-good { background: var(--good-bg); color: var(--good-fg); }
.tone-info { background: var(--info-bg); color: var(--info-fg); }
.tone-caution { background: var(--caution-bg); color: var(--caution-fg); }
.tone-warning { background: var(--warning-bg); color: var(--warning-fg); }
.table-wrap { overflow-x: auto; margin: 0 0 8px; }
table { border-collapse: collapse; width: 100%; font-size: 14px; }
th, td { text-align: left; padding: 6px 10px; border-bottom: 1px solid var(--border);
  vertical-align: top; }
th { color: var(--muted); font-weight: 600; font-size: 12px; text-transform: uppercase;
  letter-spacing: .04em; }
td { overflow-wrap: anywhere; }
.num { text-align: right; white-space: nowrap; }
.action-cell { white-space: nowrap; width: 1%; }
.cell-action { display: none; font: inherit; font-size: 12px; padding: 2px 8px;
  border-radius: 6px; border: 1px solid var(--border); background: var(--surface);
  color: var(--text); cursor: pointer; }
body.live .cell-action { display: inline-block; }
.cell-action:hover { border-color: var(--info-fg); color: var(--info-fg); }
tr.row-good td:first-child { box-shadow: inset 3px 0 var(--good-fg); }
tr.row-info td:first-child { box-shadow: inset 3px 0 var(--info-fg); }
tr.row-caution td { background: var(--caution-bg); }
tr.row-warning td { background: var(--warning-bg); }
tr.row-neutral td { color: var(--muted); }
.filter { width: 100%; padding: 10px 12px; margin: 0 0 16px; font: inherit; color: var(--text);
  background: var(--surface); border: 1px solid var(--border); border-radius: 8px; }

:root {
  --chart-surface: #fcfcfb; --chart-grid: #e1e0d9; --chart-axis: #c3c2b7; --chart-muted: #898781;
  --series-1: #2a78d6; --series-2: #eb6834; --series-3: #1baf7a;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --chart-surface: #1a1a19; --chart-grid: #2c2c2a; --chart-axis: #383835;
    --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
  }
}
:root[data-theme="dark"] {
  --chart-surface: #1a1a19; --chart-grid: #2c2c2a; --chart-axis: #383835;
  --series-1: #3987e5; --series-2: #d95926; --series-3: #199e70;
}
.hero { margin: 8px 0 16px; }
.hero-label { color: var(--muted); font-size: 13px; }
.hero-value { font-size: 52px; font-weight: 700; line-height: 1.1; letter-spacing: -0.02em; }
"""
    + CHART_CSS
)

FILTER_JS = """
document.addEventListener('input', (event) => {
  const box = event.target;
  if (!box.classList || !box.classList.contains('filter')) return;
  const body = box.closest('.page-body');
  const q = box.value.trim().toLowerCase();
  let shown = 0;
  for (const s of body.querySelectorAll('section')) {
    const hit = !q || s.textContent.toLowerCase().includes(q);
    s.hidden = !hit;
    if (hit) shown++;
  }
  body.querySelector('.no-match').hidden = shown > 0;
});
"""
