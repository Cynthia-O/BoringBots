"""Interactive run settings: similarity threshold, model and thinking effort.

The user first sees every setting with its default and can keep them all with
one keypress. Only if they decline are the settings asked one by one, each
still showing its default. Settings given as command-line flags are shown but
never asked about. Prompts go to stderr so they never mix with output on stdout.
"""

import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypeVar

from dupebot.analyze import DEFAULT_MODEL, EFFORT_LEVELS, MODEL_CHOICES
from dupebot.cluster import DEFAULT_THRESHOLD

T = TypeVar("T")
Ask = Callable[[], str]

# How "use the model's own effort" is spelled on the command line.
MODEL_DEFAULT_EFFORT = "default"


@dataclass(frozen=True)
class Settings:
    threshold: float
    model: str
    effort: str | None  # None: the model's own default


def parse_threshold(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"not a number: {text!r}") from None
    if not 0 < value <= 1:
        raise ValueError(f"must be above 0 and at most 1, got {value}")
    return value


def default_effort_of(model: str) -> str | None:
    return next((c.default_effort for c in MODEL_CHOICES if c.model == model), None)


def describe_effort(effort: str | None, model: str) -> str:
    if effort:
        return effort
    default = default_effort_of(model)
    return f"model default ({default})" if default else "model default"


def ask_settings(
    threshold: float | None = None,
    model: str | None = None,
    effort: str | None = None,
    analyzing: bool = False,
    ask: Ask = input,
    interactive: bool | None = None,
) -> Settings:
    """Settle the run's settings. Arguments are values from command-line flags,
    None where no flag was given; effort is MODEL_DEFAULT_EFFORT when the flag
    asked for the model's own default. Model and effort only matter with
    analyzing, and are only asked about then.
    """
    flagged_effort = effort is not None
    chosen = Settings(
        threshold=DEFAULT_THRESHOLD if threshold is None else threshold,
        model=model or DEFAULT_MODEL,
        effort=None if effort in (None, MODEL_DEFAULT_EFFORT) else effort,
    )
    open_threshold = threshold is None
    open_model = analyzing and model is None
    open_effort = analyzing and not flagged_effort

    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive or not (open_threshold or open_model or open_effort):
        return chosen

    _say("Settings for this run:")
    _say(f"  Similarity threshold:  {chosen.threshold}" + _origin(open_threshold, "--threshold"))
    if analyzing:
        _say(f"  Model:                 {chosen.model}" + _origin(open_model, "--model"))
        _say(
            f"  Thinking effort:       {describe_effort(chosen.effort, chosen.model)}"
            + _origin(open_effort, "--effort")
        )
    if _ask_yes_no("Keep these settings?", ask):
        return chosen

    new_threshold = _ask_threshold(chosen.threshold, ask) if open_threshold else chosen.threshold
    new_model = _ask_model(chosen.model, ask) if open_model else chosen.model
    new_effort = _ask_effort(new_model, ask) if open_effort else chosen.effort
    return Settings(new_threshold, new_model, new_effort)


def _origin(is_default: bool, flag: str) -> str:
    return "  (default)" if is_default else f"  (from {flag})"


def _ask_threshold(default: float, ask: Ask) -> float:
    _say(
        "\nFiles are clustered when their cleaned names are at least this similar, "
        "on a scale from 0 to 1.\nGo higher for fewer, tighter clusters, or lower to "
        "catch looser matches."
    )
    while True:
        reply = _prompt(f"Similarity threshold [{default}]: ", ask)
        if not reply:
            return default
        try:
            return parse_threshold(reply)
        except ValueError as e:
            _say(f"  {e}")


def _ask_model(default: str, ask: Ask) -> str:
    options = [(c.model, f"{c.model}  ({c.note})") for c in MODEL_CHOICES]
    if default not in [value for value, _ in options]:
        options.insert(0, (default, default))
    return _ask_choice("Model:", options, default, ask)


def _ask_effort(model: str, ask: Ask) -> str | None:
    options: list[tuple[str | None, str]] = [(None, describe_effort(None, model))]
    options += [(level, level) for level in EFFORT_LEVELS]
    return _ask_choice(
        "Thinking effort (more effort thinks more and costs more):", options, None, ask
    )


def _ask_choice(title: str, options: Sequence[tuple[T, str]], default: T, ask: Ask) -> T:
    default_number = next(i for i, (value, _) in enumerate(options, 1) if value == default)
    _say(f"\n{title}")
    for i, (value, label) in enumerate(options, 1):
        _say(f"  {i}) {label}" + ("  [default]" if value == default else ""))
    while True:
        reply = _prompt(f"Choose 1-{len(options)} [{default_number}]: ", ask)
        if not reply:
            return default
        if reply.isdigit() and 1 <= int(reply) <= len(options):
            return options[int(reply) - 1][0]
        _say(f"  please enter a number from 1 to {len(options)}")


def _ask_yes_no(question: str, ask: Ask) -> bool:
    while True:
        reply = _prompt(f"{question} [Y/n]: ", ask).lower()
        if reply in ("", "y", "yes"):
            return True
        if reply in ("n", "no"):
            return False
        _say("  please answer y or n")


def _prompt(text: str, ask: Ask) -> str:
    print(text, end="", file=sys.stderr, flush=True)
    try:
        return ask().strip()
    except EOFError:
        return ""  # end of input: take the default


def _say(text: str) -> None:
    print(text, file=sys.stderr)
