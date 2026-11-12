"""A row of numbered stages showing where a live app's work has got to.

Completed stages are a saturated green; stages still to come are a pale
version of the same green; the stage in progress is pale with a solid outline.
Each stage has a short detail line, so the row doubles as a progress report.
"""

import html
from collections.abc import Sequence
from dataclasses import dataclass

STATES = ("todo", "active", "done")
STATE_LABELS = {"todo": "Up next", "active": "In progress", "done": "Done ✓"}


@dataclass(frozen=True)
class Stage:
    title: str
    detail: str = ""
    state: str = "todo"  # "todo", "active" or "done"

    def __post_init__(self):
        if self.state not in STATES:
            raise ValueError(f"state must be one of {', '.join(STATES)}, got {self.state!r}")


def stages_html(stages: Sequence[Stage]) -> str:
    items = []
    for number, stage in enumerate(stages, 1):
        items.append(
            f'<li class="stage stage-{stage.state}">'
            f'<div class="stage-head"><span class="stage-number">{number}</span>'
            f'<span class="stage-title">{_e(stage.title)}</span></div>'
            f'<div class="stage-state">{STATE_LABELS[stage.state]}</div>'
            f'<div class="stage-detail">{_e(stage.detail)}</div></li>'
        )
    return f'<ol class="stages" aria-label="Stages">{"".join(items)}</ol>'


def _e(text: str) -> str:
    return html.escape(str(text), quote=True)


STAGES_CSS = """
:root {
  --stage-done-bg: #177a43; --stage-done-fg: #ffffff;
  --stage-todo-bg: #e3f4e8; --stage-todo-fg: #1f5c36;
}
@media (prefers-color-scheme: dark) {
  :root:not([data-theme="light"]) {
    --stage-done-bg: #1f8f4f; --stage-done-fg: #ffffff;
    --stage-todo-bg: #173322; --stage-todo-fg: #8fd8a8;
  }
}
:root[data-theme="dark"] {
  --stage-done-bg: #1f8f4f; --stage-done-fg: #ffffff;
  --stage-todo-bg: #173322; --stage-todo-fg: #8fd8a8;
}
.stages { list-style: none; margin: 0 0 16px; padding: 0; display: grid; gap: 8px;
  grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); counter-reset: none; }
.stage { border-radius: 10px; padding: 10px 12px; border: 2px solid transparent;
  background: var(--stage-todo-bg); color: var(--stage-todo-fg); }
.stage-done { background: var(--stage-done-bg); color: var(--stage-done-fg); }
.stage-active { border-color: var(--stage-done-bg); }
.stage-head { display: flex; align-items: center; gap: 8px; font-weight: 700; }
.stage-number { display: inline-flex; align-items: center; justify-content: center;
  width: 22px; height: 22px; border-radius: 50%; font-size: 12px;
  border: 2px solid currentColor; flex: none; }
.stage-state { font-size: 12px; font-weight: 600; text-transform: uppercase;
  letter-spacing: .04em; margin: 6px 0 2px; opacity: .85; }
.stage-detail { font-size: 13px; line-height: 1.4; overflow-wrap: anywhere; }
"""
