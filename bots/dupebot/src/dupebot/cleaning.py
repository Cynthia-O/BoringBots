"""Clean-up rules for file names, applied before clustering. No LLM involved.

Cleaning never discards information. ``clean_name`` returns the full original file
name alongside a cleaned version, and records every date, version marker and set
marker it removed. The cleaned name is only used for prefix bucketing and
similarity clustering. The original name and the recorded markers are passed on
to the LLM and the user.

The word lists and patterns below are meant to grow as we find more cases in
real corpora. All matching is case-insensitive unless noted.
"""

import re
from dataclasses import dataclass, field
from pathlib import PurePath

# --- Exclusions --------------------------------------------------------------

# OS and sync-tool clutter, matched against the full file name.
SYSTEM_FILES = frozenset({".ds_store", "thumbs.db", "desktop.ini", "icon\r"})

# Name patterns that are never clustered, matched against the full file name.
EXCLUDED_NAME_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"^~\$",  # Microsoft Office owner/lock files: ~$report.docx
        r"^\.~lock\..*#$",  # LibreOffice lock files: .~lock.report.odt#
        r"^img[_-]?\d+",  # camera and phone photos: IMG_1234.jpg
        r"^dsc[f_]?\d+",  # DSC_1234.jpg, DSCF1234.jpg, DSC01234.jpg
        r"^pxl_\d+",  # Google Pixel photos: PXL_20230115_123456.jpg
        r"^screen ?shot",  # Screenshot 2023-01-15 at 10.15.32.png, Screen Shot ...
        r"^scan[\s_-]*\d+(?:\.[^.]+)?$",  # scanner output: Scan 12.pdf, scan_0001.pdf
    )
)

# Generic titles that are never clustered, matched against the cleaned name. An
# optional trailing number is allowed, so "untitled 3" and "document1" match too.
GENERIC_TITLES = (
    "index",
    "readme",
    "read me",
    "untitled",
    "document",
    "new document",
    "notes",
    "draft",
    "test",
    "temp",
    "tmp",
    "output",
    "data",
    "main",
    "config",
    "license",
    "licence",
    "changelog",
    "resume",
    "cv",
    "invoice",
    "receipt",
)
_GENERIC_TITLE_RE = re.compile(
    r"(?:" + "|".join(re.escape(t) for t in GENERIC_TITLES) + r")(?: ?\d+)?",
    re.IGNORECASE,
)

# --- Dates -------------------------------------------------------------------
# Removed from anywhere in the name. Bare numbers other than years 1990-2039 are
# kept, so "Form 1040" stays intact.

_MONTH = (
    r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_YEAR = r"(?:19|20)\d{2}"
_MM = r"(?:0?[1-9]|1[0-2])"
_DD = r"(?:0?[1-9]|[12]\d|3[01])"
_SEP = r"[-_. ]"
_DATE_FORMS = (
    rf"{_YEAR}{_SEP}{_MM}{_SEP}{_DD}",  # 2023-01-15, 2023_1_5, 2023.01.15
    rf"{_YEAR}(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])",  # 20230115
    rf"{_DD}{_SEP}{_DD}{_SEP}{_YEAR}",  # 01-15-2023, 15.01.2023
    rf"{_MONTH}[-_. ]*{_DD}(?:st|nd|rd|th)?,?[-_. ]*{_YEAR}",  # Jan 15, 2023
    rf"{_DD}(?:st|nd|rd|th)?[-_. ]*{_MONTH}[-_. ,]*{_YEAR}",  # 15 Jan 2023
    rf"{_MONTH}[-_. ,]*{_YEAR}",  # Jan 2023, january_2023
    rf"{_YEAR}[-_. ]*{_MONTH}",  # 2023 Jan, 2023-january
    rf"{_YEAR}[-_.]{_MM}",  # 2023-01
    r"(?:199\d|20[0-3]\d)",  # bare year, 1990-2039
)
# Alternatives are tried in order, so longer forms win over the bare year.
_DATE_RE = re.compile(r"(?<![^\W_])(?:" + "|".join(_DATE_FORMS) + r")(?![^\W_])", re.IGNORECASE)

# Copy numbers added by file managers and browsers: "report (1).pdf".
_COPY_NUMBER_RE = re.compile(r"\(\d{1,3}\)")

# --- Version-series markers ---------------------------------------------------
# Stripped from the start or end of the name, repeatedly, at word boundaries.

VERSION_WORDS = frozenset(
    {
        "final",
        "finalfinal",
        "draft",
        "drafts",
        "edit",
        "edits",
        "edited",
        "redline",
        "redlined",
        "markup",
        "tracked",
        "comments",
        "reviewed",
        "revised",
        "revision",
        "updated",
        "latest",
        "orig",
        "original",
        "backup",
        "bak",
        "copy",
        "wip",
        "signed",
        "approved",
    }
)
# Version tokens: v2, v1.0, ver3, rev4, r2, 1.0, 2.1.3, final2, draft3.
_VERSION_TOKEN_RE = re.compile(
    r"(?:v|ver|rev|r)\d+(?:\.\d+)*|\d+(?:\.\d+)+|final\d+|draft\d+", re.IGNORECASE
)
# Two-word version markers.
VERSION_PHRASES = frozenset(
    {
        ("co", "edits"),
        ("co", "edit"),
        ("track", "changes"),
        ("tracked", "changes"),
        ("with", "comments"),
        ("with", "edits"),
        ("conflicted", "copy"),
        ("copy", "of"),
    }
)
# "version 2", "rev 3": one of these words followed by a number.
_VERSION_LEAD_WORDS = frozenset({"version", "ver", "rev", "revision", "v"})
_NUMBER_RE = re.compile(r"\d+(?:\.\d+)*")
# A trailing 2-3 letter token joined by "_" or "-" is treated as someone's
# initials (report_jb.docx), unless it is a set marker.
_INITIALS_RE = re.compile(r"[^\W\d_]{2,3}")

# --- Version-set markers ------------------------------------------------------
# Regions, languages and audiences. Stripped from the start or end of the name.

SET_WORDS = frozenset(
    {
        "usa",
        "emea",
        "apac",
        "latam",
        "amer",
        "intl",
        "international",
        "global",
        "english",
        "german",
        "french",
        "spanish",
        "italian",
        "portuguese",
        "dutch",
        "japanese",
        "chinese",
        "korean",
        "internal",
        "external",
        "public",
        "print",
        "web",
        "mobile",
    }
)
# Two-letter region and language codes. These only count when written in capitals
# in the original name ("_DE", not "_de"), because many are also ordinary words.
SET_CODES_UPPERCASE = frozenset(
    {
        "US", "UK", "GB", "DE", "FR", "ES", "IT", "NL", "BE", "CH", "AT", "SE", "NO", "DK",
        "FI", "IE", "PT", "PL", "JP", "CN", "KR", "IN", "BR", "MX", "CA", "AU", "NZ", "SG",
        "HK", "ZA", "EU", "NA", "EN",
    }
)  # fmt: skip
# "for_executives", "for new hires": "for" within the last three words starts an
# audience marker that runs to the end of the name.
_AUDIENCE_WORD = "for"

# A token is a run of letters/digits, with dotted numbers kept whole (v1.0, 2.1.3).
_TOKEN_RE = re.compile(r"[^\W_]+(?:\.\d+)*")


@dataclass(frozen=True)
class CleanedName:
    original: str  # full file name, extension included, exactly as on disk
    cleaned: str  # lowercase words used for matching; "" if nothing was left
    dates: tuple[str, ...] = ()
    version_markers: tuple[str, ...] = ()
    set_markers: tuple[str, ...] = ()


@dataclass
class _Token:
    sep: str  # the separator text before this token in the original name
    text: str  # original case

    @property
    def lower(self) -> str:
        return self.text.lower()


@dataclass
class _Found:
    dates: list[str] = field(default_factory=list)
    version: list[str] = field(default_factory=list)
    sets: list[str] = field(default_factory=list)


def clean_name(filename: str) -> CleanedName:
    """Clean a file name for clustering, recording everything that was removed."""
    suffix = PurePath(filename).suffix
    stem = filename[: -len(suffix)] if suffix else filename
    found = _Found()

    # Dates and copy numbers can sit anywhere in the name.
    found.dates = [m.group(0) for m in _DATE_RE.finditer(stem)]
    stem = _DATE_RE.sub(" ", stem)
    found.version = [m.group(0) for m in _COPY_NUMBER_RE.finditer(stem)]
    stem = _COPY_NUMBER_RE.sub(" ", stem)

    tokens = _tokenize(stem)
    prefix_found = _Found()
    suffix_found = _Found()  # collected end-first, reversed below
    while tokens:
        if _strip_suffix(tokens, suffix_found) or _strip_prefix(tokens, prefix_found):
            continue
        break

    return CleanedName(
        original=filename,
        cleaned=" ".join(t.lower for t in tokens),
        dates=tuple(found.dates),
        version_markers=tuple(prefix_found.version + found.version + suffix_found.version[::-1]),
        set_markers=tuple(prefix_found.sets + suffix_found.sets[::-1]),
    )


def exclusion_reason(name: CleanedName) -> str | None:
    """Return why a file should never be clustered, or None if it is a candidate.

    Names that are empty after cleaning are not covered here; callers check
    ``name.cleaned`` for that.
    """
    if name.original.lower() in SYSTEM_FILES:
        return "excluded"
    if any(p.search(name.original) for p in EXCLUDED_NAME_PATTERNS):
        return "excluded"
    if name.cleaned and _GENERIC_TITLE_RE.fullmatch(name.cleaned):
        return "excluded"
    return None


def _tokenize(stem: str) -> list[_Token]:
    tokens = []
    end = 0
    for m in _TOKEN_RE.finditer(stem):
        tokens.append(_Token(sep=stem[end : m.start()], text=m.group(0)))
        end = m.end()
    return tokens


def _joined(tokens: list[_Token]) -> str:
    """Original text of consecutive tokens, with their inner separators."""
    return tokens[0].text + "".join(t.sep + t.text for t in tokens[1:])


def _is_version_token(token: _Token) -> bool:
    return token.lower in VERSION_WORDS or bool(_VERSION_TOKEN_RE.fullmatch(token.text))


def _is_set_token(token: _Token) -> bool:
    return token.lower in SET_WORDS or token.text in SET_CODES_UPPERCASE


def _strip_suffix(tokens: list[_Token], found: _Found) -> bool:
    """Remove one marker from the end of tokens. Return True if one was removed."""
    words = [t.lower for t in tokens]
    if len(tokens) >= 2:
        last_two = tokens[-2:]
        if tuple(words[-2:]) in VERSION_PHRASES or (
            words[-2] in _VERSION_LEAD_WORDS and _NUMBER_RE.fullmatch(words[-1])
        ):
            found.version.append(_joined(last_two))
            del tokens[-2:]
            return True
        for i in range(max(1, len(tokens) - 3), len(tokens) - 1):
            if words[i] == _AUDIENCE_WORD:
                found.sets.append(_joined(tokens[i:]))
                del tokens[i:]
                return True

    last = tokens[-1]
    if _is_version_token(last):
        found.version.append(last.text)
    elif _is_set_token(last):
        found.sets.append(last.text)
    elif len(tokens) >= 2 and _INITIALS_RE.fullmatch(last.text) and last.sep in ("_", "-"):
        found.version.append(last.text)
    else:
        return False
    tokens.pop()
    return True


def _strip_prefix(tokens: list[_Token], found: _Found) -> bool:
    """Remove one marker from the start of tokens. Return True if one was removed."""
    if len(tokens) >= 2 and (tokens[0].lower, tokens[1].lower) in VERSION_PHRASES:
        found.version.append(_joined(tokens[:2]))
        del tokens[:2]
    elif _is_version_token(tokens[0]):
        found.version.append(tokens.pop(0).text)
    elif _is_set_token(tokens[0]):
        found.sets.append(tokens.pop(0).text)
    else:
        return False
    if tokens:
        tokens[0].sep = ""
    return True
