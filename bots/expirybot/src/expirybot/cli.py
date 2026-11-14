"""Command-line entry point.

ExpiryBot may create new files but never edits, deletes, moves or renames
existing ones. It makes no model calls yet: the survey and flagging are plain
Python, and the taxonomy is written by hand.
"""

import argparse
import os
import sys
from datetime import date
from pathlib import Path

from bottrace import Tracer, span
from botview import open_in_browser, write_page
from expirybot.flag import DEFAULT_SOON_DAYS, flag, write_expired_list
from expirybot.survey import survey
from expirybot.taxonomy import TaxonomyError, load_taxonomy
from expirybot.view import flag_page, summary_line, survey_page


def home_dir() -> Path:
    """Where ExpiryBot keeps what it writes, outside the repo."""
    configured = os.environ.get("EXPIRYBOT_HOME")
    return Path(configured).expanduser() if configured else Path.home() / ".expirybot"


def pages_dir() -> Path:
    return home_dir() / "pages"


def _date_arg(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date (use YYYY-MM-DD): {text!r}") from None


def _days_arg(text: str) -> int:
    if not text.isdigit():
        raise argparse.ArgumentTypeError(f"not a whole number of days: {text!r}")
    return int(text)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="expirybot",
        description="Flags documents past their retention period. Reports only; never "
        "deletes, moves or edits anything.",
        epilog="Without --taxonomy, ExpiryBot surveys the folder and shows which dates its "
        "files carry. See bots/expirybot/examples/taxonomy.json for the taxonomy format.",
    )
    parser.add_argument("root", type=Path, help="Folder to review")
    parser.add_argument(
        "--taxonomy",
        type=Path,
        help="Retention taxonomy (JSON) to flag the folder against.",
    )
    parser.add_argument(
        "--today",
        type=_date_arg,
        help="Flag as if today were this date (YYYY-MM-DD), to try a taxonomy out.",
    )
    parser.add_argument(
        "--soon-days",
        type=_days_arg,
        default=DEFAULT_SOON_DAYS,
        help=f"Files expiring within this many days are listed as expiring soon "
        f"(default {DEFAULT_SOON_DAYS}).",
    )
    parser.add_argument(
        "--expired-list",
        type=Path,
        help="Also write the expired files to this new .json or .csv file, for example to "
        "keep them out of a RAG index. Refuses to write over an existing file.",
    )
    parser.add_argument(
        "--no-browser", action="store_true", help="Save the page but do not open it."
    )
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")
    if args.expired_list and not args.taxonomy:
        parser.error("--expired-list needs --taxonomy")
    if args.expired_list and args.expired_list.suffix.lower() not in (".json", ".csv"):
        parser.error("--expired-list must end in .json or .csv")
    if args.expired_list and args.expired_list.exists():
        parser.error(f"will not write over an existing file: {args.expired_list}")
    taxonomy = None
    if args.taxonomy:
        try:
            taxonomy = load_taxonomy(args.taxonomy)
        except TaxonomyError as e:
            print(f"Problems with {args.taxonomy}:", file=sys.stderr)
            for problem in e.problems:
                print(f"  - {problem}", file=sys.stderr)
            return 2

    with Tracer("ExpiryBot").trace(
        "command line",
        folder=str(args.root),
        taxonomy=str(args.taxonomy or ""),
    ):
        surveyed = survey(args.root)
        print(f"Surveyed {len(surveyed.files):,} files in {surveyed.root}.", file=sys.stderr)
        if taxonomy is None:
            page = survey_page(surveyed)
            name = "expirybot-survey"
        else:
            result = flag(surveyed, taxonomy, args.today or date.today(), args.soon_days)
            print(summary_line(result), file=sys.stderr)
            if args.expired_list:
                try:
                    n = write_expired_list(result, args.expired_list)
                except FileExistsError:
                    parser.error(f"will not write over an existing file: {args.expired_list}")
                print(f"Listed {n:,} expired files in {args.expired_list}.", file=sys.stderr)
            page = flag_page(result)
            name = "expirybot-flags"
        with span("write page", kind="stage"):
            path = write_page(page, pages_dir(), name=name)

    print(f"Page: {path}", file=sys.stderr)
    if not args.no_browser and not open_in_browser(path):
        print("Could not open a browser; open the file above yourself.", file=sys.stderr)
    return 0
