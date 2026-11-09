"""Command-line entry point.

DupeBot only reports. It never deletes, moves or edits files, and never writes
over an existing file.
"""

import argparse
import json
import sys
from collections.abc import Callable
from pathlib import Path

from dupebot.cluster import DEFAULT_THRESHOLD, MIN_SIZE_BYTES, cluster_records, scan
from dupebot.report import build_report


def parse_threshold(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise ValueError(f"not a number: {text!r}") from None
    if not 0 < value <= 1:
        raise ValueError(f"must be above 0 and at most 1, got {value}")
    return value


def ask_threshold(ask: Callable[[], str] = input, interactive: bool | None = None) -> float:
    """Ask the user for a similarity threshold, defaulting to DEFAULT_THRESHOLD.

    Prompts go to stderr so they never mix with the JSON report on stdout. When
    stdin is not a terminal, the default is used without asking.
    """
    if interactive is None:
        interactive = sys.stdin.isatty()
    if not interactive:
        return DEFAULT_THRESHOLD
    print(
        "Files are clustered when their cleaned names are at least this similar, "
        "on a scale from 0 to 1.\n"
        "Go higher for fewer, tighter clusters, or lower to catch looser matches.",
        file=sys.stderr,
    )
    while True:
        print(f"Similarity threshold [{DEFAULT_THRESHOLD}]: ", end="", file=sys.stderr, flush=True)
        try:
            reply = ask().strip()
        except EOFError:
            return DEFAULT_THRESHOLD
        if not reply:
            return DEFAULT_THRESHOLD
        try:
            return parse_threshold(reply)
        except ValueError as e:
            print(f"  {e}", file=sys.stderr)


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
        help=f"Name similarity threshold, 0 to 1 (default {DEFAULT_THRESHOLD}). "
        "If omitted, you are asked.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Write the JSON report to this new file instead of stdout. "
        "Refuses to write over an existing file.",
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")
    if args.output and args.output.exists():
        parser.error(f"will not write over an existing file: {args.output}")

    threshold = args.threshold if args.threshold is not None else ask_threshold()
    scan_result = scan(args.root, MIN_SIZE_BYTES)
    clusters = cluster_records(scan_result.records, threshold)
    report = build_report(args.root, scan_result, clusters, threshold, MIN_SIZE_BYTES)
    text = json.dumps(report, indent=2, ensure_ascii=False) + "\n"

    if args.output:
        try:
            with args.output.open("x", encoding="utf-8") as f:  # "x" never overwrites
                f.write(text)
        except FileExistsError:
            parser.error(f"will not write over an existing file: {args.output}")
    else:
        sys.stdout.write(text)

    summary = report["summary"]
    print(
        f"{summary['clusters']} clusters covering {summary['files_in_clusters']} of "
        f"{summary['candidate_files']} candidate files.",
        file=sys.stderr,
    )
    # TODO: stage 2 (agent analyzes clusters) and stage 3 (agent explains findings).
    return 0
