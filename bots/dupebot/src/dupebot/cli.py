"""Command-line entry point.

DupeBot only reports. It never deletes, moves or edits files, and never writes
over an existing file.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from bottrace import Tracer, current_trace_id, span
from botview import LiveServer, open_in_browser, write_page
from dupebot import apierrors
from dupebot.analyze import (
    DEFAULT_MODEL,
    EFFORT_LEVELS,
    analyze,
    batch_clusters,
)
from dupebot.calllog import CallLog, default_db_path
from dupebot.cluster import DEFAULT_THRESHOLD, cluster_records, scan
from dupebot.env import load_dotenv
from dupebot.report import build_report
from dupebot.settings import MODEL_DEFAULT_EFFORT, ask_settings, describe_effort, parse_threshold
from dupebot.ui import DupeBotApp
from dupebot.view import estimated_cost, results_page, summary_lines


def _threshold_arg(text: str) -> float:
    try:
        return parse_threshold(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dupebot",
        description="Find likely duplicate files. Reports only; never deletes. With no "
        "other flags, opens DupeBot's browser app: choose the folder and settings, scan and "
        "send from there, with a running token tally. Each window you open (the page's New "
        "window link) runs separately. Stop it with Ctrl+C.",
    )
    parser.add_argument(
        "root",
        type=Path,
        nargs="?",
        help="Directory to scan. Optional for the browser app, where it can be typed in the "
        "page; needed with --json, --output or --analyze.",
    )
    parser.add_argument(
        "--threshold",
        type=_threshold_arg,
        help=f"Name similarity threshold, 0 to 1 (default {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Run in the terminal instead of the browser app, and write the JSON report to "
        "this new file. Refuses to write over an existing file.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Run in the terminal instead of the browser app, and print the JSON report.",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Run in the terminal instead of the browser app: send the clusters to the "
        "analyst model and open its analysis as a page. Needs ANTHROPIC_API_KEY (see "
        ".env.example). Asks first.",
    )
    parser.add_argument(
        "--model",
        help=f"Model (default {DEFAULT_MODEL}). In the browser app, preselects it.",
    )
    parser.add_argument(
        "--effort",
        choices=(MODEL_DEFAULT_EFFORT, *EFFORT_LEVELS),
        help="Thinking effort. Lower effort thinks less and uses fewer "
        f"output tokens. '{MODEL_DEFAULT_EFFORT}' (the default) leaves the model's own.",
    )
    # The browser app is the default; --ui is still accepted from before that.
    parser.add_argument("--ui", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "--traces",
        action="store_true",
        help="In the browser app, add a snapshot of recent traces (as `uv run traces` shows them, "
        "across all windows and bots) to the foot of each window.",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open a browser: the browser app prints its address, and --analyze "
        "saves its results page without opening it.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="With --analyze, send without asking for confirmation.",
    )
    parser.epilog = (
        "In the terminal, settings not given as flags are shown before the run, with the "
        "option to keep all the defaults or change them one by one."
    )
    args = parser.parse_args(argv)

    in_terminal = bool(args.json or args.output or args.analyze)
    if args.root is not None and not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")
    if in_terminal:
        if args.ui or args.traces:
            parser.error("--json, --output and --analyze run in the terminal, not the browser app")
        if args.root is None:
            parser.error("give the directory to scan")
        if args.json and args.output:
            parser.error("choose --json or --output, not both")
        if args.output and args.output.exists():
            parser.error(f"will not write over an existing file: {args.output}")
    if args.yes and not args.analyze:
        parser.error("--yes works with --analyze")
    if not in_terminal:
        load_dotenv()
        root = args.root.resolve() if args.root else None  # the page needs a full path

        # One DupeBot per browser window, so windows can run different experiments.
        def new_window_app() -> DupeBotApp:
            return DupeBotApp(
                root, args.threshold, args.model, args.effort, show_traces=args.traces
            )

        LiveServer(new_window_app).serve(open_browser=not args.no_browser)
        return 0

    settings = ask_settings(args.threshold, args.model, args.effort, analyzing=args.analyze)
    # One traced request per run, from after the settings questions until it ends.
    with Tracer("DupeBot").trace(
        "command line", folder=str(args.root), analyze=args.analyze, threshold=settings.threshold
    ):
        return _run(args, parser, settings)


def _run(args, parser, settings) -> int:
    scan_result = scan(args.root)
    clusters = cluster_records(scan_result.records, settings.threshold)
    report = build_report(args.root, scan_result, clusters, settings.threshold)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"

    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as f:  # "x" never overwrites
                f.write(text)
        except FileExistsError:
            parser.error(f"will not write over an existing file: {args.output}")
    elif args.json:
        sys.stdout.write(text)

    summary = report["summary"]
    print(
        f"{summary['clusters']} clusters covering {summary['files_in_clusters']} of "
        f"{summary['candidate_files']} candidate files.",
        file=sys.stderr,
    )
    if args.analyze:
        return run_analysis(
            report,
            settings.model,
            confirmed=args.yes,
            effort=settings.effort,
            open_browser=not args.no_browser,
        )
    # TODO: stage 3 (agent explains findings).
    return 0


def run_analysis(
    report: dict,
    model: str,
    confirmed: bool = False,
    ask: Callable[[], str] = input,
    interactive: bool | None = None,
    effort: str | None = None,
    open_browser: bool = True,
) -> int:
    """Send the clusters to the model, save the analysis as a page and open it.
    Returns an exit code."""
    clusters = report["clusters"]
    if not clusters:
        print("No clusters to analyse.", file=sys.stderr)
        return 0
    n_files = sum(len(c["files"]) for c in clusters)
    n_requests = len(batch_clusters(clusters))
    if not confirmed:
        if interactive is None:
            interactive = sys.stdin.isatty()
        if not interactive:
            print("Not sending: rerun with --yes to send without being asked.", file=sys.stderr)
            return 1
        print(
            f"About to send {len(clusters)} clusters ({n_files} files) to {model} "
            f"(thinking effort: {describe_effort(effort, model)}) "
            f"in {n_requests} request(s).\n"
            "Sent: each file's path below the scanned folder, size, modified time and "
            "authors. Not sent: file contents.",
            file=sys.stderr,
        )
        print("Send? [y/N]: ", end="", file=sys.stderr, flush=True)
        with span("waiting for your go-ahead", kind="wait") as wait:
            try:
                reply = ask().strip().lower()
            except EOFError:
                reply = ""
            wait.set(answer=reply or "(none)")
        if reply not in ("y", "yes"):
            print("Not sent.", file=sys.stderr)
            return 1

    load_dotenv()  # before opening the log, so DUPEBOT_DB in .env takes effect
    log = CallLog()
    try:
        results = analyze(report, model=model, log=log, effort=effort, run_id=current_trace_id())
    except apierrors.EXPECTED as e:
        print(apierrors.describe(e, model), file=sys.stderr)
        return 1
    finally:
        log.close()
        print(f"LLM calls are logged in {log.path}", file=sys.stderr)

    for line in summary_lines(results):
        print(line)
    tokens_in = sum(r.input_tokens for r in results)
    tokens_out = sum(r.output_tokens for r in results)
    print(
        f"Used {tokens_in:,} input and {tokens_out:,} output tokens "
        f"(estimated cost {estimated_cost(results, model)}).",
        file=sys.stderr,
    )
    warnings = [w for r in results for w in r.warnings]
    if warnings:
        print(f"{len(warnings)} problem(s) with the answer; see the page.", file=sys.stderr)

    answers = {c["cluster_id"]: c for r in results for c in r.clusters}
    page = results_page(
        report,
        answers,
        model,
        describe_effort(effort, model),
        cost=estimated_cost(results, model),
        warnings=[w for r in results for w in r.warnings],
    )
    path = write_page(page, pages_dir(), name="dupebot-results")
    print(f"Results page: {path}", file=sys.stderr)
    if open_browser and not open_in_browser(path):
        print("Could not open a browser; open the file above yourself.", file=sys.stderr)
    return 0


def pages_dir() -> Path:
    """Results pages go next to the call log, outside the repo."""
    return default_db_path().parent / "pages"
