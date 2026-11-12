"""`spend`: open a dashboard of every bot's estimated model spend, by day."""

import argparse
import sys
from pathlib import Path

from botview import open_in_browser, write_page
from finops.dashboard import dashboard_page
from finops.ledger import build_ledger
from finops.pricing import usd

DASHBOARD_DIR = Path.home() / ".boringbots" / "dashboards"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="spend",
        description="Open a dashboard of estimated model spend across all BoringBots, by day. "
        "Reads the bots' call logs read-only and writes the dashboard as a new file.",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Write the dashboard but do not open it."
    )
    args = parser.parse_args(argv)

    ledger = build_ledger()
    path = write_page(dashboard_page(ledger), DASHBOARD_DIR, name="spend")
    print(
        f"{ledger.total.calls:,} calls, estimated {usd(ledger.total.cost.total)} to date.",
        file=sys.stderr,
    )
    print(f"Dashboard: {path}", file=sys.stderr)
    if not args.no_browser and not open_in_browser(path):
        print("Could not open a browser; open the file above yourself.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
