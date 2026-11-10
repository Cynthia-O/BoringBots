"""Command-line entry point.

DupeBot only reports. It never deletes, moves or edits files, and never writes
over an existing file.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

import anthropic

from dupebot.analyze import (
    DEFAULT_MODEL,
    EFFORT_LEVELS,
    AnalysisError,
    analyze,
    batch_clusters,
)
from dupebot.calllog import CallLog
from dupebot.cluster import DEFAULT_THRESHOLD, cluster_records, scan
from dupebot.env import load_dotenv
from dupebot.report import build_report
from dupebot.settings import MODEL_DEFAULT_EFFORT, ask_settings, describe_effort, parse_threshold


def _threshold_arg(text: str) -> float:
    try:
        return parse_threshold(text)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="dupebot",
        description="Find likely duplicate files. Reports only; never deletes.",
    )
    parser.add_argument("root", type=Path, help="Directory to scan")
    parser.add_argument(
        "--threshold",
        type=_threshold_arg,
        help=f"Name similarity threshold, 0 to 1 (default {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the JSON report to this new file instead of stdout. "
        "Refuses to write over an existing file.",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="Send the clusters to the analyst model and print its analysis instead of "
        "the JSON report. Needs ANTHROPIC_API_KEY (see .env.example). Asks first.",
    )
    parser.add_argument(
        "--model",
        help=f"Model for --analyze (default {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--effort",
        choices=(MODEL_DEFAULT_EFFORT, *EFFORT_LEVELS),
        help="Thinking effort for --analyze. Lower effort thinks less and uses fewer "
        f"output tokens. '{MODEL_DEFAULT_EFFORT}' (the default) leaves the model's own.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="With --analyze, send without asking for confirmation.",
    )
    parser.epilog = (
        "Settings not given as flags are shown before the run, with the option to keep "
        "all the defaults or change them one by one."
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")
    if args.output and args.output.exists():
        parser.error(f"will not write over an existing file: {args.output}")

    settings = ask_settings(args.threshold, args.model, args.effort, analyzing=args.analyze)
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
    elif not args.analyze:
        sys.stdout.write(text)

    summary = report["summary"]
    print(
        f"{summary['clusters']} clusters covering {summary['files_in_clusters']} of "
        f"{summary['candidate_files']} candidate files.",
        file=sys.stderr,
    )
    if args.analyze:
        return run_analysis(report, settings.model, confirmed=args.yes, effort=settings.effort)
    # TODO: stage 3 (agent explains findings).
    return 0


def run_analysis(
    report: dict,
    model: str,
    confirmed: bool = False,
    ask: Callable[[], str] = input,
    interactive: bool | None = None,
    effort: str | None = None,
) -> int:
    """Send the clusters to the model and print its analysis. Returns an exit code."""
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
        try:
            reply = ask().strip().lower()
        except EOFError:
            reply = ""
        if reply not in ("y", "yes"):
            print("Not sent.", file=sys.stderr)
            return 1

    load_dotenv()  # before opening the log, so DUPEBOT_DB in .env takes effect
    log = CallLog()
    try:
        results = analyze(report, model=model, log=log, effort=effort)
    except anthropic.CredentialsError:
        print(_NO_KEY, file=sys.stderr)
        return 1
    except anthropic.AuthenticationError:
        print(f"The API key was rejected. {_NO_KEY}", file=sys.stderr)
        return 1
    except anthropic.NotFoundError:
        print(f"Model not found: {model}", file=sys.stderr)
        return 1
    except anthropic.RateLimitError:
        print("Rate limited by the API. Wait a minute and try again.", file=sys.stderr)
        return 1
    except anthropic.APIStatusError as e:
        print(f"The API returned an error ({e.status_code}): {e.message}", file=sys.stderr)
        return 1
    except anthropic.APIConnectionError:
        print("Could not reach the API. Check your internet connection.", file=sys.stderr)
        return 1
    except AnalysisError as e:
        print(f"Analysis failed: {e}", file=sys.stderr)
        return 1
    finally:
        log.close()
        print(f"LLM calls are logged in {log.path}", file=sys.stderr)

    for result in results:
        print(f"## Clusters {', '.join(result.cluster_ids)}\n")
        print(result.text.strip() + "\n")
    tokens_in = sum(r.input_tokens for r in results)
    tokens_out = sum(r.output_tokens for r in results)
    print(f"Used {tokens_in:,} input and {tokens_out:,} output tokens.", file=sys.stderr)
    return 0


_NO_KEY = (
    "No Anthropic API key found. Copy .env.example to .env in the BoringBots folder, "
    "add your key, and run DupeBot from that folder."
)
