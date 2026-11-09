"""Command-line entry point.

CatalogBot may create new files but never edits, deletes, moves or renames existing ones.
"""

import argparse
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="catalogbot", description="Writes a README in each folder describing what that folder and its subfolders contain.")
    parser.add_argument("root", type=Path, help="Directory to catalog")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")

    print(f"CatalogBot is not implemented yet. Would run on {args.root}.")
    return 0
