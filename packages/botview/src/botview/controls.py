"""Settings controls for live botview apps: toggles for choices, sliders for
numbers, text fields for free text such as a folder path.

Each control knows its default, which is always marked on the page, so the
user can see at a glance what they have changed.
"""

import html
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Option:
    value: str
    label: str
    note: str = ""  # small text under the label, e.g. a price comparison


@dataclass(frozen=True)
class Choice:
    """A set of toggle buttons, one of which is on."""

    name: str
    label: str
    options: Sequence[Option]
    default: str
    selected: str | None = None  # starts on the default when None
    help: str = ""

    def __post_init__(self):
        values = [o.value for o in self.options]
        if self.default not in values or (self.selected or self.default) not in values:
            raise ValueError(f"{self.name}: default and selected must be among the options")


@dataclass(frozen=True)
class Slider:
    name: str
    label: str
    minimum: float
    maximum: float
    step: float
    default: float
    selected: float | None = None
    help: str = ""


@dataclass(frozen=True)
class TextField:
    """One line of free text. The app checks what the text means (for example,
    that a folder exists); botview only trims it and limits its length."""

    name: str
    label: str
    default: str
    selected: str | None = None
    placeholder: str = ""
    help: str = ""
    max_length: int = 1000
    # Shown but not editable; the server ignores what the page sends and uses default.
    locked: bool = False


Control = Choice | Slider | TextField


def controls_html(controls: Sequence[Control]) -> str:
    def one(c: Control) -> str:
        if isinstance(c, Choice):
            return _choice(c)
        if isinstance(c, Slider):
            return _slider(c)
        return _text_field(c)

    return "\n".join(one(c) for c in controls)


def parse_values(controls: Sequence[Control], raw: Mapping[str, object]) -> dict[str, object]:
    """Check values sent back by the page against the controls. Raises ValueError."""
    values: dict[str, object] = {}
    for c in controls:
        value = raw.get(c.name)
        if isinstance(c, Choice):
            if value not in [o.value for o in c.options]:
                raise ValueError(f"{c.label}: not one of the options: {value!r}")
            values[c.name] = value
        elif isinstance(c, TextField) and c.locked:
            values[c.name] = c.default  # never taken from the page
        elif isinstance(c, TextField):
            if not isinstance(value, str):
                raise ValueError(f"{c.label}: missing")
            text = value.strip()
            if len(text) > c.max_length:
                raise ValueError(f"{c.label}: longer than {c.max_length} characters")
            values[c.name] = text
        else:
            try:
                number = float(value)  # type: ignore[arg-type]
            except (TypeError, ValueError):
                raise ValueError(f"{c.label}: not a number: {value!r}") from None
            if not c.minimum <= number <= c.maximum:
                raise ValueError(f"{c.label}: must be from {c.minimum} to {c.maximum}")
            values[c.name] = number
    return values


def _choice(c: Choice) -> str:
    selected = c.selected or c.default
    buttons = []
    for o in c.options:
        is_default = o.value == c.default
        tag = '<span class="default-tag">default</span>' if is_default else ""
        note = f'<span class="note">{_e(o.note)}</span>' if o.note else ""
        buttons.append(
            f'<label class="toggle{" is-default" if is_default else ""}">'
            f'<input type="radio" name="{_e(c.name)}" value="{_e(o.value)}"'
            f"{' checked' if o.value == selected else ''}>"
            f'<span class="face"><span class="label">{_e(o.label)}{tag}</span>{note}</span></label>'
        )
    return (
        f'<fieldset class="control"><legend>{_e(c.label)}</legend>'
        f'<div class="toggles">{"".join(buttons)}</div>{_help(c.help)}</fieldset>'
    )


def _slider(c: Slider) -> str:
    value = c.default if c.selected is None else c.selected
    return (
        f'<fieldset class="control slider" data-default="{c.default}">'
        f"<legend>{_e(c.label)}</legend>"
        f'<div class="slider-row"><input type="range" name="{_e(c.name)}" '
        f'min="{c.minimum}" max="{c.maximum}" step="{c.step}" value="{value}" '
        f'list="{_e(c.name)}-ticks">'
        f'<output class="value">{value}</output>'
        f'<span class="default-tag">default</span>'
        f'<button type="button" class="reset">Reset to {c.default}</button></div>'
        f'<datalist id="{_e(c.name)}-ticks"><option value="{c.default}"></option></datalist>'
        f"{_help(c.help)}</fieldset>"
    )


def _text_field(c: TextField) -> str:
    if c.locked:
        return (
            f'<fieldset class="control textfield locked">'
            f'<legend><label for="field-{_e(c.name)}">{_e(c.label)}</label></legend>'
            f'<div class="slider-row"><input type="text" id="field-{_e(c.name)}" '
            f'name="{_e(c.name)}" value="{_e(c.default)}" readonly aria-readonly="true">'
            f'<span class="fixed-tag">fixed</span></div>{_help(c.help)}</fieldset>'
        )
    value = c.default if c.selected is None else c.selected
    return (
        f'<fieldset class="control textfield" data-default="{_e(c.default)}">'
        f'<legend><label for="field-{_e(c.name)}">{_e(c.label)}</label></legend>'
        f'<div class="slider-row"><input type="text" id="field-{_e(c.name)}" '
        f'name="{_e(c.name)}" value="{_e(value)}" placeholder="{_e(c.placeholder)}" '
        f'maxlength="{c.max_length}" spellcheck="false" autocomplete="off">'
        f'<span class="default-tag">default</span>'
        f'<button type="button" class="reset">Reset</button></div>'
        f"{_help(c.help)}</fieldset>"
    )


def _help(text: str) -> str:
    return f'<p class="help">{_e(text)}</p>' if text else ""


def _e(text: object) -> str:
    return html.escape(str(text), quote=True)


CONTROLS_CSS = """
.controls { background: var(--surface); border: 1px solid var(--border); border-radius: 10px;
  padding: 16px; margin: 0 0 16px; display: grid; gap: 16px; }
.control { border: 0; margin: 0; padding: 0; min-width: 0; }
.control legend { font-weight: 600; margin-bottom: 6px; padding: 0; }
.toggles { display: flex; flex-wrap: wrap; gap: 6px; }
.toggle input { position: absolute; opacity: 0; pointer-events: none; }
.toggle .face { display: flex; flex-direction: column; padding: 6px 12px; border-radius: 8px;
  border: 1px solid var(--border); cursor: pointer; background: var(--bg); min-width: 72px; }
.toggle.is-default .face { border-style: dashed; border-color: var(--info-fg); }
.toggle input:checked + .face { background: var(--info-bg); border: 1px solid var(--info-fg);
  color: var(--info-fg); }
.toggle input:focus-visible + .face { outline: 2px solid var(--info-fg); outline-offset: 2px; }
.toggle .label { font-weight: 600; display: flex; gap: 6px; align-items: center; }
.toggle .note, .help { font-size: 12px; color: var(--muted); margin: 4px 0 0; }
.default-tag { font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 999px;
  background: var(--info-bg); color: var(--info-fg); border: 1px dashed var(--info-fg); }
.slider-row { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.slider-row input[type=range] { flex: 1 1 220px; accent-color: var(--info-fg); }
.slider-row .value { font-weight: 700; font-variant-numeric: tabular-nums; min-width: 3em; }
.slider:not(.at-default) .default-tag, .textfield:not(.at-default) .default-tag {
  visibility: hidden; }
.slider.at-default .reset, .textfield.at-default .reset { display: none; }
.textfield input[type=text] { flex: 1 1 280px; font: inherit; color: var(--text);
  background: var(--bg); border: 1px solid var(--border); border-radius: 8px; padding: 8px 10px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 14px; }
.textfield.at-default input[type=text] { border-style: dashed; border-color: var(--info-fg); }
.textfield.locked input[type=text] { color: var(--muted); background: var(--surface);
  cursor: default; }
.fixed-tag { font-size: 11px; font-weight: 600; padding: 0 6px; border-radius: 999px;
  background: var(--neutral-bg); color: var(--neutral-fg); }
button { font: inherit; border-radius: 8px; border: 1px solid var(--border); padding: 6px 12px;
  background: var(--bg); color: var(--text); cursor: pointer; }
button.primary { background: var(--info-fg); color: var(--surface); border-color: var(--info-fg);
  font-weight: 600; padding: 8px 18px; }
button:disabled { opacity: .5; cursor: progress; }
.actions { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
.status { color: var(--muted); }
.confirm { background: var(--surface); border: 1px solid var(--info-fg); border-radius: 10px;
  padding: 16px; margin: 0 0 16px; }
.confirm p { white-space: pre-line; }
.error { background: var(--warning-bg); color: var(--warning-fg); border-radius: 8px;
  padding: 8px 12px; margin: 0 0 16px; }
.app-footer { margin-top: 32px; }
"""

CONTROLS_JS = """
for (const field of document.querySelectorAll('.slider')) {
  const input = field.querySelector('input');
  const out = field.querySelector('output');
  const dflt = field.dataset.default;
  const sync = () => {
    out.textContent = Number(input.value).toFixed(2);
    field.classList.toggle('at-default', Math.abs(input.value - dflt) < 1e-9);
  };
  input.addEventListener('input', sync);
  field.querySelector('.reset').addEventListener('click', () => { input.value = dflt; sync(); });
  sync();
}
for (const field of document.querySelectorAll('.textfield:not(.locked)')) {
  const input = field.querySelector('input');
  const dflt = field.dataset.default;
  const sync = () => field.classList.toggle('at-default', input.value.trim() === dflt);
  input.addEventListener('input', sync);
  field.querySelector('.reset').addEventListener('click', () => { input.value = dflt; sync(); });
  sync();
}
"""


def to_json_script(name: str, value: object) -> str:
    """A JS constant holding value, safe to put inside a <script> element."""
    encoded = json.dumps(value).replace("</", "<\\/")  # cannot close the <script> early
    return f"const {name} = {encoded};\n"
