"""Stage 1: survey the folder. Plain Python, no LLM, read-only.

Records, for every file, the facts later stages need: where it is, its size,
and every date that could say how old it is. Nothing is opened except to read
the dates some formats store inside the file.
"""

import os
import stat
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path, PurePosixPath

from bottrace import current_span, traced
from filefacts.clutter import is_clutter
from filefacts.embedded import DATED_EXTENSIONS, read_dates
from filefacts.namedates import NameDate, latest_date

# Where a document's age can be counted from, in the order they are shown.
DATE_SOURCES = (
    "modified",
    "created",
    "name",
    "folder",
    "inside_modified",
    "inside_created",
)
SOURCE_LABELS = {
    "modified": "Modified (on this disk)",
    "created": "Created (on this disk)",
    "name": "Date in file name",
    "folder": "Date in folder name",
    "inside_modified": "Modified (stored inside file)",
    "inside_created": "Created (stored inside file)",
}


@dataclass(frozen=True)
class FileFacts:
    path: Path  # absolute
    relpath: PurePosixPath  # below the surveyed folder
    size_bytes: int
    modified: date
    created: date | None = None  # macOS records this; some filesystems don't
    name_date: NameDate | None = None  # latest date written in the file name
    folder_date: NameDate | None = None  # latest date in the nearest dated folder name
    inside_modified: date | None = None
    inside_created: date | None = None

    @property
    def extension(self) -> str:
        return self.path.suffix.lower()

    def date_from(self, source: str) -> date | None:
        """The file's date from one source, or None if it has none."""
        if source in ("name", "folder"):
            found = self.name_date if source == "name" else self.folder_date
            return found.date if found else None
        return getattr(self, source)

    def evidence(self, source: str) -> str:
        """What a source's date was read from, for showing next to it."""
        if source == "name" and self.name_date:
            return f'"{self.name_date.text}" in the name'
        if source == "folder" and self.folder_date:
            return f'"{self.folder_date.text}" in a folder name'
        return SOURCE_LABELS[source]


@dataclass
class Survey:
    root: Path
    files: list[FileFacts] = field(default_factory=list)
    skipped: dict[str, int] = field(default_factory=lambda: defaultdict(int))


@traced("survey folder", kind="stage")
def survey(root: Path) -> Survey:
    """Facts about every file under root. Hidden folders (such as .git) are
    surveyed like any other: the taxonomy decides where they belong. System
    clutter is skipped and counted; symlinks are not followed."""
    root = root.resolve()
    result = Survey(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames.sort()
        for filename in sorted(filenames):
            path = Path(dirpath) / filename
            if is_clutter(filename):
                result.skipped["clutter"] += 1
                continue
            try:
                info = path.lstat()
            except OSError:
                result.skipped["unreadable"] += 1
                continue
            if not stat.S_ISREG(info.st_mode):
                continue  # symlinks, sockets and the like
            result.files.append(_facts(root, path, info))
    current_span().set(files=len(result.files), skipped=sum(result.skipped.values()))
    return result


def _facts(root: Path, path: Path, info: os.stat_result) -> FileFacts:
    relpath = PurePosixPath(path.relative_to(root).as_posix())
    birth = getattr(info, "st_birthtime", None)
    inside = read_dates(path) if path.suffix.lower() in DATED_EXTENSIONS else None
    return FileFacts(
        path=path,
        relpath=relpath,
        size_bytes=info.st_size,
        modified=date.fromtimestamp(info.st_mtime),
        created=date.fromtimestamp(birth) if birth else None,
        name_date=latest_date(PurePosixPath(path.name).stem),
        folder_date=_folder_date(relpath),
        inside_modified=inside.modified if inside else None,
        inside_created=inside.created if inside else None,
    )


def hidden_folder(relpath: PurePosixPath) -> PurePosixPath | None:
    """The outermost hidden folder (name starting with ".") on a file's path, or
    None. For Projects/app/.git/objects/ab/cd it is Projects/app/.git."""
    parts = relpath.parent.parts
    for i, part in enumerate(parts):
        if part.startswith("."):
            return PurePosixPath(*parts[: i + 1])
    return None


def _folder_date(relpath: PurePosixPath) -> NameDate | None:
    """The latest date in the nearest folder (deepest first) whose name has one."""
    for folder in reversed(relpath.parent.parts):
        found = latest_date(folder)
        if found:
            return found
    return None
