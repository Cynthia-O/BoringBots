"""Dates written in file and folder names: found, and read as calendar dates.

DATE_RE finds them (DupeBot strips its matches from names before clustering).
find_dates also reads each match as a date. Bare numbers other than years
1990-2039 are not dates, so "Form 1040" is left alone.

When a match leaves room for doubt, the latest date it could mean is used:
- a month counts as its last day ("March 2023" is 2023-03-31), and a bare
  year as 31 December;
- "01-02-2023" could be 2 January or 1 February, so it is read as 1 February.
Retention counted from the latest possible date can only expire a file later,
never earlier.
"""

import calendar
import re
from dataclasses import dataclass
from datetime import date

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
DATE_RE = re.compile(r"(?<![^\W_])(?:" + "|".join(_DATE_FORMS) + r")(?![^\W_])", re.IGNORECASE)

# The same forms again, with named parts, for reading a match as a date.
_PARSERS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        rf"(?P<y>{_YEAR}){_SEP}(?P<m>{_MM}){_SEP}(?P<d>{_DD})",
        rf"(?P<y>{_YEAR})(?P<m>\d\d)(?P<d>\d\d)",
        rf"(?P<a>{_DD}){_SEP}(?P<b>{_DD}){_SEP}(?P<y>{_YEAR})",
        rf"(?P<mon>{_MONTH})[-_. ]*(?P<d>{_DD})(?:st|nd|rd|th)?,?[-_. ]*(?P<y>{_YEAR})",
        rf"(?P<d>{_DD})(?:st|nd|rd|th)?[-_. ]*(?P<mon>{_MONTH})[-_. ,]*(?P<y>{_YEAR})",
        rf"(?P<mon>{_MONTH})[-_. ,]*(?P<y>{_YEAR})",
        rf"(?P<y>{_YEAR})[-_. ]*(?P<mon>{_MONTH})",
        rf"(?P<y>{_YEAR})[-_.](?P<m>{_MM})",
        rf"(?P<y>{_YEAR})",
    )
)

_MONTHS = {name: n for n, name in enumerate(calendar.month_abbr) if name}


@dataclass(frozen=True)
class NameDate:
    text: str  # as written in the name
    date: date  # the latest date the text can mean
    precision: str  # "day", "month" or "year"


def find_dates(text: str) -> list[NameDate]:
    """Every date written in text, in order. Matches that are not real dates
    (such as 2023-02-30) are left out."""
    found = []
    for m in DATE_RE.finditer(text):
        parsed = parse_date(m.group(0))
        if parsed:
            found.append(parsed)
    return found


def latest_date(text: str) -> NameDate | None:
    """The latest date written in text, or None. "Statement 2023-01 to 2023-03"
    gives 2023-03-31."""
    return max(find_dates(text), key=lambda d: d.date, default=None)


def parse_date(text: str) -> NameDate | None:
    """Read one DATE_RE match as a date."""
    for parser in _PARSERS:
        m = parser.fullmatch(text)
        if m:
            return _read(text, m.groupdict())
    return None


def _read(text: str, parts: dict[str, str | None]) -> NameDate | None:
    year = int(parts["y"])
    if parts.get("mon"):
        month = _MONTHS[parts["mon"][:3].title()]
    elif parts.get("m"):
        month = int(parts["m"])
    else:
        month = None

    if parts.get("a"):  # day and month in either order: take the later reading
        a, b = int(parts["a"]), int(parts["b"])
        readings = [_day(year, a, b), _day(year, b, a)]
        valid = [d for d in readings if d]
        return NameDate(text, max(valid), "day") if valid else None
    if parts.get("d"):
        day = _day(year, month, int(parts["d"]))
        return NameDate(text, day, "day") if day else None
    if month:
        if not 1 <= month <= 12:
            return None
        return NameDate(text, date(year, month, calendar.monthrange(year, month)[1]), "month")
    return NameDate(text, date(year, 12, 31), "year")


def _day(year: int, month: int | None, day: int) -> date | None:
    try:
        return date(year, month or 0, day)
    except ValueError:
        return None
