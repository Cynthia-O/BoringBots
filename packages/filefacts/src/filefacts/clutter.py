"""Operating-system and application clutter: files no person made on purpose."""

import re

# OS and sync-tool clutter, matched against the full file name, lowercased.
SYSTEM_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini", "icon\r"})

# Lock files that office suites leave next to an open document.
LOCK_FILE_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^~\$",  # Microsoft Office owner/lock files: ~$report.docx
        r"^\.~lock\..*#$",  # LibreOffice lock files: .~lock.report.odt#
    )
)


def is_clutter(filename: str) -> bool:
    """True for system files and lock files."""
    return filename.lower() in SYSTEM_FILES or any(p.search(filename) for p in LOCK_FILE_PATTERNS)
