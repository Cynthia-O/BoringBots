"""Command-line entry point.

ExpiryBot may create new files but never edits, deletes, moves or renames existing ones.
"""

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="expirybot", description="Helps you agree on a retention policy, then flags documents past their retention period.")
    parser.add_argument("root", type=Path, help="Directory to review")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")

    print(f"ExpiryBot is not implemented yet. Would run on {args.root}.")
    return 0
