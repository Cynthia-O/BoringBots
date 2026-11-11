"""DupeBot in the browser: pick settings, scan, review the clusters, then send.

Each round is two steps, so money is only spent after a look at what will be
sent: "Scan" clusters the folder with the chosen threshold and shows the
clusters (free); "Send" passes those clusters to the chosen model. A running
tally of tokens and estimated cost sits at the bottom of the page. It belongs to the session, so it
starts at zero each time DupeBot's browser app is started.
"""

import hashlib
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from botview import (
    Action,
    Badge,
    Choice,
    Confirm,
    Option,
    Reply,
    Section,
    Slider,
    Table,
    Text,
    TextField,
    write_page,
)
from dupebot import apierrors
from dupebot.analyze import (
    DEFAULT_MODEL,
    EFFORT_LEVELS,
    MODEL_CHOICES,
    analyze,
    batch_clusters,
)
from dupebot.calllog import CallLog, default_db_path
from dupebot.cluster import DEFAULT_THRESHOLD, cluster_records, scan
from dupebot.pricing import PRICES_CHECKED, Cost, call_cost, usd
from dupebot.report import build_report
from dupebot.settings import MODEL_DEFAULT_EFFORT, describe_effort
from dupebot.view import analysis_page, clusters_page


@dataclass
class Usage:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0  # billed: thinking plus the visible answer
    thinking_tokens: int = 0
    thinking_unreported: int = 0  # calls whose thinking count the API did not give
    cost: Cost = field(default_factory=Cost)
    unpriced: int = 0  # calls to a model with no known price, left out of cost

    @property
    def response_tokens(self) -> int:
        return self.output_tokens - self.thinking_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add_call(self, row: dict) -> None:
        input_tokens = row.get("input_tokens") or 0
        output_tokens = row.get("output_tokens") or 0
        thinking = row.get("thinking_tokens")
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if thinking is None:
            if output_tokens:
                self.thinking_unreported += 1
        else:
            self.thinking_tokens += thinking
        cost = call_cost(row["model"], input_tokens, output_tokens, thinking)
        if cost is None:
            self.unpriced += 1
        else:
            self.cost += cost


@dataclass
class TokenTally:
    started: datetime = field(default_factory=datetime.now)
    session: Usage = field(default_factory=Usage)
    last: Usage | None = None
    last_label: str = ""

    def add_run(self, rows: list[dict], label: str) -> None:
        last = Usage()
        for row in rows:
            last.add_call(row)
            self.session.add_call(row)
        self.last, self.last_label = last, label

    def section(self, log_path: Path) -> Section:
        s, last = self.session, self.last

        def tokens(usage: Usage | None, attr: str) -> str:
            return "" if usage is None else f"{getattr(usage, attr):,}"

        def dollars(usage: Usage | None, attr: str) -> str:
            return "" if usage is None else usd(getattr(usage.cost, attr))

        def row(label: str, token_attr: str, cost_attr: str | None) -> list[str]:
            return [
                label,
                tokens(s, token_attr),
                dollars(s, cost_attr) if cost_attr else "",
                tokens(last, token_attr),
                dollars(last, cost_attr) if cost_attr else "",
            ]

        rows = [
            row("Input (prompt and clusters)", "input_tokens", "input"),
            row("Output: response", "response_tokens", "response"),
            row("Output: thinking", "thinking_tokens", "thinking"),
            row("Output billed (response + thinking)", "output_tokens", "output"),
            row("Total", "total_tokens", "total"),
            row("API calls", "calls", None),
        ]
        blocks = [
            Table(
                ["", "Session tokens", "Session cost", "Last run tokens", "Last run cost"],
                rows,
                row_tones=[None, None, None, None, "info", None],
                numeric=frozenset({1, 2, 3, 4}),
            ),
            Text(f"Last run: {self.last_label}." if last else "No runs sent yet this session."),
            Text(
                "Token counts are as the API reported them. A run can be several calls when "
                f"the clusters are split into batches. Every call is also kept in {log_path}."
            ),
        ]
        if s.thinking_unreported:
            blocks.append(
                Text(
                    f"For {s.thinking_unreported} call(s) the API did not report thinking "
                    "separately, so their output is counted as response.",
                    tone="caution",
                )
            )
        if s.unpriced:
            blocks.append(
                Text(
                    f"{s.unpriced} call(s) used a model with no price in pricing.py and are "
                    "left out of the costs.",
                    tone="warning",
                )
            )
        checked = date.fromisoformat(PRICES_CHECKED)
        blocks.append(
            Text(
                f"Prices last checked {checked:%-d %B %Y}. Prices change over time, so "
                "these costs are estimates at the standard API prices on that date "
                "(dupebot/pricing.py). Your Claude Console invoice is the final word.",
                fine_print=True,
            )
        )
        return Section(
            "Token usage and estimated cost",
            [
                Badge(f"{usd(s.cost.total)} this session", "info"),
                Badge(f"session started {self.started:%H:%M}"),
                Badge("resets on restart"),
            ],
            blocks,
        )


@dataclass
class _Pending:
    report: dict
    model: str
    effort: str | None


class DupeBotApp:
    """The botview App for DupeBot. One instance is one session."""

    run_label = "Scan folder"

    def __init__(
        self,
        root: Path | None = None,  # the folder field starts with this; empty when None
        threshold: float | None = None,
        model: str | None = None,
        effort: str | None = None,
        client=None,  # an Anthropic client; tests pass a stand-in
    ):
        self.client = client
        self.title = "DupeBot"
        self.subtitle = (
            "Choose settings, scan to see the clusters (free), then send them to the model."
        )
        self.facts: list[tuple[str, str]] = []
        model_options = [Option(c.model, c.label or c.model, c.note) for c in MODEL_CHOICES]
        known = [o.value for o in model_options]
        self.controls = [
            TextField(
                "folder",
                "Folder to scan",
                default=str(root) if root else "",
                placeholder="~/Documents",
                help="The full path of a folder on this Mac; ~ means your home folder. "
                "Subfolders are included. To copy a folder's path in Finder, right-click it, "
                "hold Option, and choose Copy as Pathname.",
            ),
            Slider(
                "threshold",
                "Name similarity threshold (Jaccard)",
                minimum=0.05,
                maximum=1.0,
                step=0.01,
                default=DEFAULT_THRESHOLD,
                selected=threshold,
                help="Files are clustered when their cleaned names share at least this "
                "share of three-letter chunks. Higher gives fewer, tighter clusters; lower "
                "catches looser matches. (Jaccard distance is 1 minus this.)",
            ),
            Choice(
                "model",
                "Model",
                model_options,
                default=DEFAULT_MODEL,
                selected=model if model in known else None,
            ),
            Choice(
                "effort",
                "Thinking effort",
                [Option(MODEL_DEFAULT_EFFORT, "Model default", _model_defaults())]
                + [Option(level, level) for level in EFFORT_LEVELS],
                default=MODEL_DEFAULT_EFFORT,
                selected=effort,
                help="More effort means more thinking, billed as output tokens but not shown.",
            ),
        ]
        self.tally = TokenTally()
        self._pending: dict[str, _Pending] = {}
        # Files this session's scans found, by an opaque id. "Show in Finder"
        # only accepts these ids, never a path sent by the page.
        self._files: dict[str, Path] = {}

    def footer(self) -> list[Section]:
        return [self.tally.section(default_db_path())]

    def handle(self, action: str, values: dict, data: dict) -> Reply:
        if action == "run":
            return self._scan(values)
        if action == "send":
            return self._send(str(data.get("pending", "")))
        if action == "reveal":
            return self._reveal(str(data.get("file", "")))
        return Reply(error=f"Unknown action: {action}")

    def _scan(self, values: dict) -> Reply:
        threshold = round(float(values["threshold"]), 2)
        model = values["model"]
        effort = None if values["effort"] == MODEL_DEFAULT_EFFORT else values["effort"]
        folder = str(values["folder"])
        if not folder:
            return Reply(error="Enter the folder to scan.")
        root = Path(folder).expanduser()
        if not root.is_absolute():
            return Reply(error=f"Enter the full path of the folder, starting with / or ~: {folder}")
        if not root.is_dir():
            return Reply(error=f"Not a folder (or not readable): {root}")
        scan_result = scan(root)
        clusters = cluster_records(scan_result.records, threshold)
        report = build_report(root, scan_result, clusters, threshold)
        page = clusters_page(report, self._reveal_button)
        if not report["clusters"]:
            return Reply(page=page, status="No clusters at this threshold.")
        self._pending = {}  # only the latest scan can be sent
        pending_id = uuid.uuid4().hex
        self._pending[pending_id] = _Pending(report, model, effort)
        n_files = sum(len(c["files"]) for c in report["clusters"])
        n_requests = len(batch_clusters(report["clusters"]))
        text = (
            f"Send {len(report['clusters'])} clusters ({n_files} files) to {model} "
            f"(thinking effort: {describe_effort(effort, model)}) in {n_requests} request(s)?\n"
            "Sent: each file's path below the scanned folder, size, modified time and "
            "authors. Not sent: file contents."
        )
        return Reply(
            page=page,
            confirm=Confirm(text, "Send to model", "send", {"pending": pending_id}),
            status=f"Found {len(report['clusters'])} clusters.",
        )

    def _send(self, pending_id: str) -> Reply:
        pending = self._pending.pop(pending_id, None)
        if pending is None:
            return Reply(error="That scan is no longer current. Scan again, then send.")
        run_id = uuid.uuid4().hex
        log = CallLog()
        try:
            results = analyze(
                pending.report,
                model=pending.model,
                client=self.client,
                log=log,
                effort=pending.effort,
                run_id=run_id,
            )
        except apierrors.EXPECTED as e:
            return Reply(error=apierrors.describe(e, pending.model))
        finally:
            # Counted from the log, so billed calls that failed are included too.
            rows = log.rows(run_id)
            log.close()
            if rows:
                self.tally.add_run(rows, f"{datetime.now():%H:%M}, {pending.model}")
        effort = describe_effort(pending.effort, pending.model)
        page = analysis_page(pending.report, results, pending.model, effort, self._reveal_button)
        path = write_page(page, default_db_path().parent / "pages", name="dupebot-results")
        warnings = sum(len(r.warnings) for r in results)
        status = f"Done. Saved a copy to {path}"
        if warnings:
            status += f" ({warnings} problem(s) with the answer; see the top of the results)"
        return Reply(page=page, status=status)

    def _reveal_button(self, path: str) -> Action:
        file_id = hashlib.sha256(path.encode("utf-8")).hexdigest()[:16]
        self._files[file_id] = Path(path)
        return Action(
            "Show in Finder",
            "reveal",
            {"file": file_id},
            title="Open this file's folder in Finder with the file selected",
        )

    def _reveal(self, file_id: str) -> Reply:
        """Show a scanned file in a new Finder window, so several can be open side
        by side. Read-only: Finder opens a window on the file's folder with the
        file selected; nothing is changed."""
        path = self._files.get(file_id)
        if path is None:
            return Reply(error="That file is not from a scan in this session. Scan again.")
        if not path.is_absolute():
            return Reply(error=f"Not a full path: {path}")
        if not os.path.lexists(path):
            return Reply(error=f"No longer there (moved, renamed or deleted?): {path}")
        if sys.platform != "darwin":
            return Reply(error="Show in Finder only works on a Mac.")
        # Arguments are passed as a list, never through a shell, and the path reaches
        # the AppleScript as an argument rather than as script text, so nothing in a
        # file name can run as a command.
        try:
            done = subprocess.run(
                ["osascript", "-e", NEW_FINDER_WINDOW_SCRIPT, str(path)],
                capture_output=True,
                text=True,
                timeout=60,  # the first use waits for the macOS permission prompt
            )
        except subprocess.TimeoutExpired:
            return Reply(
                status="Waiting for macOS to allow controlling Finder. Answer its prompt, "
                "then click Show in Finder again."
            )
        if done.returncode == 0:
            return Reply(status=f"Showing {path.name} in a new Finder window.")
        # Not allowed to control Finder (or Finder said no): fall back to a plain
        # reveal, which works without permission but may reuse an existing window.
        fallback = subprocess.run(["open", "-R", str(path)], capture_output=True, timeout=10)
        if fallback.returncode != 0:
            return Reply(error=f"Finder could not show {path}")
        if "-1743" in done.stderr or "not allowed" in done.stderr.lower():
            return Reply(
                status=f"Showing {path.name} in Finder, possibly reusing a window. For a "
                "new window each time, allow controlling Finder in System Settings > "
                "Privacy & Security > Automation (under the app running DupeBot, e.g. "
                "Visual Studio Code or Terminal)."
            )
        return Reply(status=f"Showing {path.name} in Finder.")


# Opens a new Finder window on the folder of the file given as the first
# argument and selects the file. Only opens and selects; changes nothing.
# Sending these commands to Finder needs one-time permission from macOS.
NEW_FINDER_WINDOW_SCRIPT = """
on run argv
    set theFile to (POSIX file (item 1 of argv)) as alias
    tell application "Finder"
        set theWindow to make new Finder window
        set target of theWindow to (container of theFile)
        select theFile
        activate
    end tell
end run
"""


def _model_defaults() -> str:
    return " · ".join(f"{c.label or c.model}: {c.default_effort}" for c in MODEL_CHOICES)
