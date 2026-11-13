"""`traces`: open a page of recent traces across all bots."""

import argparse
import sys
from pathlib import Path

from bottrace.trace import read_traces
from bottrace.view import traces_page
from botview import open_in_browser, write_page

PAGES_DIR = Path.home() / ".boringbots" / "dashboards"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="traces",
        description="Open a page of recent bot requests: a timeline of each step, where the "
        "time went, agent turns, tokens and estimated cost. Reads the trace file read-only.",
    )
    parser.add_argument("--limit", type=int, default=30, help="Requests to list (default 30).")
    parser.add_argument(
        "--detail", type=int, default=5, help="Newest requests to show timelines for (default 5)."
    )
    parser.add_argument("--bot", help="Only this bot's requests, e.g. DupeBot.")
    parser.add_argument("--no-browser", action="store_true", help="Write the page only.")
    args = parser.parse_args(argv)

    traces = read_traces(limit=args.limit if not args.bot else args.limit * 10)
    if args.bot:
        traces = [t for t in traces if t and t[0]["bot"].lower() == args.bot.lower()][: args.limit]
    path = write_page(traces_page(traces, detail=args.detail), PAGES_DIR, name="traces")
    print(f"{len(traces)} request(s). Page: {path}", file=sys.stderr)
    if not args.no_browser and not open_in_browser(path):
        print("Could not open a browser; open the file above yourself.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
