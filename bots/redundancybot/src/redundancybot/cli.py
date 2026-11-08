"""Command-line entry point.

RedundancyBot only reports. It never deletes, moves or edits files.
"""

import argparse
from pathlib import Path

from redundancybot.cluster import group_by_extension, scan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="redundancybot",
        description="Find likely duplicate files. Reports only; never deletes.",
    )
    parser.add_argument("root", type=Path, help="Directory to scan")
    args = parser.parse_args(argv)

    if not args.root.is_dir():
        parser.error(f"not a directory: {args.root}")

    groups = group_by_extension(scan(args.root))
    for ext, files in sorted(groups.items()):
        print(f"{ext or '(no extension)'}: {len(files)} files")
    # TODO: stage 2 (agent analyzes clusters) and stage 3 (agent explains findings).
    return 0
