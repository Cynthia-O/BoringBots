"""Command-line entry point.

TidyBot may create new files but never edits, deletes, moves or renames existing ones.
"""

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tidybot", description="Runs DupeBot, ExpiryBot and CatalogBot together for corpus-wide clean-up jobs.")
    parser.add_argument("root", type=Path, help="Directory to tidy")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")

    print(f"TidyBot is not implemented yet. Would run on {args.root}.")
    return 0
