"""ExpiryBot's browser pages (via the shared botview package)."""

from collections import Counter, defaultdict
from datetime import date

from botview import Badge, Page, Section, Span, Table, Text, Timeline
from expirybot.flag import Flagged, FlagResult
from expirybot.survey import DATE_SOURCES, SOURCE_LABELS, FileFacts, Survey, hidden_folder
from expirybot.taxonomy import FOREVER

# Long lists are cut to this many rows on the page; the expired list file
# (--expired-list) always has every expired file.
MAX_ROWS = 500
# The folders chart shows this many folders per page, and colours this many
# file types (the commonest across the whole folder); the rest are "Other".
FOLDERS_PER_PAGE = 10
TOP_TYPES = 6
OTHER_TYPES = "Other"
SKIP_LABELS = {
    "clutter": "system and lock files",
    "unreadable": "unreadable",
}


def survey_page(survey: Survey) -> Page:
    """What the folder holds and which dates its files carry: the facts for
    writing (or checking) a taxonomy."""
    files = survey.files
    n = len(files)
    sections = [
        Section(
            "Date sources",
            blocks=[
                Text(
                    "How many files carry each kind of date. Each group in a taxonomy picks "
                    "the order in which these are tried."
                ),
                Table(
                    ["Source", "Files with one", "Share"],
                    [
                        [
                            SOURCE_LABELS[s],
                            f"{c:,}",
                            _share(c, n),
                        ]
                        for s in DATE_SOURCES
                        for c in [sum(1 for f in files if f.date_from(s))]
                    ],
                    numeric=frozenset({1, 2}),
                ),
            ],
        ),
        _folders_section(survey),
        _hidden_section(survey.files),
    ]
    return Page(
        title="ExpiryBot survey",
        subtitle="Read-only. Nothing was sent anywhere.",
        facts=[
            ("Folder", str(survey.root)),
            ("Files", f"{n:,}"),
            ("Total size", _size(sum(f.size_bytes for f in files))),
            *_skipped_facts(survey),
        ],
        sections=sections,
    )


def flag_page(result: FlagResult) -> Page:
    counts = result.counts()
    expired = result.with_status("expired")
    notices = [
        Text(
            "These are flags only. ExpiryBot has not deleted, moved or changed anything; "
            "what to delete is your decision.",
            tone="info",
        )
    ]
    if counts["undated"]:
        notices.append(
            Text(
                f"{counts['undated']:,} file(s) have none of the dates their group's age_date "
                "lists, so their age is unknown. Add a fallback source (modified is always "
                "present) to that group.",
                tone="caution",
            )
        )
    return Page(
        title="ExpiryBot: expired documents",
        subtitle=f"As of {result.today:%d %B %Y}",
        hero=(
            "Past their retention period",
            f"{len(expired):,} files ({_size(sum(f.file.size_bytes for f in expired))})",
        ),
        facts=[
            ("Folder", str(result.survey.root)),
            ("Taxonomy", str(result.taxonomy.source or result.taxonomy.name)),
            ("Expiring soon means", f"within {result.soon_days} days"),
            *[(_STATUS_TITLES[s], f"{counts[s]:,}") for s in _STATUS_ORDER],
            *_skipped_facts(result.survey),
        ],
        notices=notices,
        sections=[
            _groups_section(result),
            *[_status_section(result, s) for s in _STATUS_ORDER],
            _hidden_section(result.survey.files, result),
        ],
    )


_STATUS_ORDER = ("expired", "soon", "undated", "kept", "unmatched")
_STATUS_TITLES = {
    "expired": "Expired",
    "soon": "Expiring soon",
    "kept": "Kept",
    "undated": "No usable date",
    "unmatched": "Not in any group",
}
_STATUS_TONES = {"expired": "warning", "soon": "caution", "undated": "caution"}


def _groups_section(result: FlagResult) -> Section:
    by_group: dict[str, Counter] = defaultdict(Counter)
    for f in result.files:
        if f.placement:
            by_group[f.placement.label][f.status] += 1
    rows = []
    labels: list[str] = []
    for group, depth in result.taxonomy.walk():
        labels = labels[:depth] + [group.name]
        label = " / ".join(labels)
        c = by_group.get(label, Counter())
        rows.append(
            [
                " " * depth + group.name,
                _keep(group.keep_years) if group.keep_years is not None else "(inherited)",
                ", ".join(group.age_date) if group.age_date else "(inherited)",
                f"{sum(c.values()):,}",
                f"{c['expired']:,}",
                f"{c['soon']:,}",
                f"{c['undated']:,}",
            ]
        )
    return Section(
        "Groups",
        blocks=[
            Text(
                "Files counted in a group are those not caught by one of its subgroups. "
                "The first group that matches a file wins."
            ),
            Table(
                ["Group", "Keep", "Age counted from", "Files", "Expired", "Soon", "No date"],
                rows,
                numeric=frozenset({3, 4, 5, 6}),
            ),
        ],
    )


def _status_section(result: FlagResult, status: str) -> Section:
    files = result.with_status(status)
    files.sort(key=lambda f: (f.expires_on or date.max, str(f.file.relpath)))
    if status == "unmatched":
        columns = ["File", "Modified", "Size"]
        rows = [
            [str(f.file.relpath), f.file.modified.isoformat(), _size(f.file.size_bytes)]
            for f in files
        ]
        numeric = frozenset({2})
    elif status == "undated":
        columns = ["File", "Group", "Sources tried"]
        rows = [
            [str(f.file.relpath), f.placement.label, ", ".join(f.placement.age_date)] for f in files
        ]
        numeric = frozenset()
    else:
        columns = ["File", "Group", "Age date", "Read from", "Expires", "Size"]
        rows = [_dated_row(f, result.today) for f in files]
        numeric = frozenset({5})
    blocks = []
    if not files:
        blocks.append(Text("None."))
    else:
        if len(rows) > MAX_ROWS:
            blocks.append(
                Text(f"Showing the first {MAX_ROWS:,} of {len(rows):,}.", fine_print=True)
            )
        tone = _STATUS_TONES.get(status)
        shown = rows[:MAX_ROWS]
        blocks.append(Table(columns, shown, row_tones=[tone] * len(shown), numeric=numeric))
    return Section(
        _STATUS_TITLES[status],
        badges=[Badge(f"{len(files):,} files", _STATUS_TONES.get(status, "neutral"))],
        blocks=blocks,
    )


def _dated_row(f: Flagged, today: date) -> list[str]:
    if f.expires_on is None:
        expires = "never (kept forever)"
    elif f.expires_on < today:
        expires = f"{f.expires_on.isoformat()} ({_span(today - f.expires_on)} ago)"
    else:
        expires = f"{f.expires_on.isoformat()} (in {_span(f.expires_on - today)})"
    return [
        str(f.file.relpath),
        f.placement.label,
        f.age_date.isoformat(),
        f.file.evidence(f.age_source),
        expires,
        _size(f.file.size_bytes),
    ]


def _folders_section(survey: Survey) -> Section:
    by_folder: dict[str, list[FileFacts]] = defaultdict(list)
    for f in survey.files:
        top = f.relpath.parts[0] + "/" if len(f.relpath.parts) > 1 else "(top level)"
        by_folder[top].append(f)
    # Most files first; ties by name.
    largest_first = sorted(by_folder.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    categories = _type_categories(survey.files)
    rows = [
        [
            folder,
            f"{len(fs):,}",
            _size(sum(f.size_bytes for f in fs)),
            min(f.modified for f in fs).isoformat(),
            max(f.modified for f in fs).isoformat(),
            _type_summary(fs),
        ]
        for folder, fs in largest_first
    ]
    blocks: list = []
    if by_folder:
        blocks.append(_folders_timeline(survey.files, largest_first, categories))
    blocks.append(
        Table(
            ["Folder", "Files", "Size", "Oldest modified", "Newest modified", "File types"],
            rows[:MAX_ROWS],
            numeric=frozenset({1, 2}),
        )
    )
    return Section("Folders", blocks=blocks)


def _folders_timeline(
    files: list[FileFacts],
    largest_first: list[tuple[str, list[FileFacts]]],
    categories: list[str],
) -> Timeline:
    """Each top-level folder's span of modified dates and its mix of file types,
    under one bar for the whole folder's dates and above one bar for its whole
    mix. The file types, their colours and the date axis come from the whole
    folder, so they stay the same on every page."""
    spans = [
        Span(
            folder,
            min(f.modified for f in fs),
            max(f.modified for f in fs),
            detail=f"{len(fs):,} files",
            breakdown=_type_counts(fs, categories),
        )
        for folder, fs in largest_first[:MAX_ROWS]
    ]
    return Timeline(
        "Folders at a glance",
        spans,
        subtitle="Top-level folders, most files first: when their files were modified, and "
        "what types they are (share of files).",
        overall_label="Whole folder",
        categories=categories,
        overall_breakdown=_type_counts(files, categories),
        breakdown_label="All files",
        breakdown_unit="files",
        other_category=OTHER_TYPES,
        span_heading="Modified",
        breakdown_heading="File types",
        page_size=FOLDERS_PER_PAGE,
        item_name="folders",
    )


def _type_categories(files: list[FileFacts]) -> list[str]:
    """The most common file types across the whole folder, plus "Other" for the
    rest when there are more."""
    counts = Counter(_type_of(f) for f in files)
    top = [ext for ext, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]
    if len(top) <= TOP_TYPES:
        return top
    return [*top[:TOP_TYPES], OTHER_TYPES]


def _type_counts(files: list[FileFacts], categories: list[str]) -> list[int]:
    named = set(categories) - {OTHER_TYPES}
    counts = Counter(t if (t := _type_of(f)) in named else OTHER_TYPES for f in files)
    return [counts[c] for c in categories]


def _type_summary(files: list[FileFacts]) -> str:
    """The commonest types in a set of files, as text: ".pdf 60%, .docx 25%, other 15%"."""
    counts = Counter(_type_of(f) for f in files)
    shown = counts.most_common(3)
    parts = [f"{t} {100 * n / len(files):.0f}%" for t, n in shown]
    rest = len(files) - sum(n for _, n in shown)
    if rest:
        parts.append(f"other {100 * rest / len(files):.0f}%")
    return ", ".join(parts)


def _type_of(f: FileFacts) -> str:
    return f.extension or "(none)"


def _hidden_section(files: list[FileFacts], result: FlagResult | None = None) -> Section:
    """Hidden folders (names starting with "."), outermost only, so each can be
    given a place in the taxonomy like any other folder. With a flag result,
    shows which groups their files landed in."""
    by_folder: dict[str, list[FileFacts]] = defaultdict(list)
    for f in files:
        folder = hidden_folder(f.relpath)
        if folder:
            by_folder[f"{folder}/"].append(f)
    placed = {id(f.file): f for f in result.files} if result else {}
    columns = ["Hidden folder", "Files", "Size", "Newest modified"]
    if result:
        columns.append("Where its files are")
    rows = []
    for folder, fs in sorted(by_folder.items()):
        row = [
            folder,
            f"{len(fs):,}",
            _size(sum(f.size_bytes for f in fs)),
            max(f.modified for f in fs).isoformat(),
        ]
        if result:
            where = Counter(
                placed[id(f)].placement.label if placed[id(f)].placement else "not in any group"
                for f in fs
            )
            row.append(", ".join(f"{label} ({n:,})" for label, n in where.most_common()))
        rows.append(row)
    blocks: list = [
        Text(
            "Folders whose names start with a dot, which Finder hides: app and tool data "
            "such as .git or .venv, sync and system folders, or folders someone hid on "
            "purpose. They are surveyed like any other folder; give each a place in the "
            'taxonomy (a group with "folders": [".git"] catches everything inside).'
        )
    ]
    if rows:
        blocks.append(Table(columns, rows[:MAX_ROWS], numeric=frozenset({1, 2})))
    else:
        blocks.append(Text("None."))
    return Section("Hidden folders", badges=[Badge(f"{len(rows):,} folders")], blocks=blocks)


def _skipped_facts(survey: Survey) -> list[tuple[str, str]]:
    return [
        (f"Skipped: {SKIP_LABELS.get(reason, reason)}", f"{n:,}")
        for reason, n in sorted(survey.skipped.items())
        if n
    ]


def summary_line(result: FlagResult) -> str:
    c = result.counts()
    return (
        f"{c['expired']:,} expired, {c['soon']:,} expiring within {result.soon_days} days, "
        f"{c['kept']:,} kept, {c['undated']:,} with no usable date, "
        f"{c['unmatched']:,} in no group ({len(result.files):,} files)."
    )


def _keep(years: int | str) -> str:
    return "forever" if years == FOREVER else f"{years} year{'s' if years != 1 else ''}"


def _span(delta) -> str:
    days = delta.days
    if days < 60:
        return f"{days} day{'s' if days != 1 else ''}"
    if days < 730:
        return f"{round(days / 30.44)} months"
    return f"{days / 365.25:.1f} years"


def _share(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "-"


def _size(n: int) -> str:
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return ""
