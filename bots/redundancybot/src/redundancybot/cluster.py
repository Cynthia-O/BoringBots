"""Stage 1: deterministic clustering. No LLM involved.

Groups files by extension, keeping only extensions with more than one file.
Name-similarity clustering within each group is still to come.
"""

from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path


def group_by_extension(paths: Iterable[Path]) -> dict[str, list[Path]]:
    """Map lowercase extension -> files, dropping extensions with only one file.

    Files with no extension are grouped under the empty string.
    """
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in paths:
        groups[path.suffix.lower()].append(path)
    return {ext: files for ext, files in groups.items() if len(files) > 1}


def scan(root: Path) -> list[Path]:
    """List every regular file under root, recursively. Read-only."""
    return [p for p in root.rglob("*") if p.is_file()]
