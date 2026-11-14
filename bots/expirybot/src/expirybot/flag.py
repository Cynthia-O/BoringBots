"""Stage 5: flag documents past their retention period. Plain Python, no LLM.

Every surveyed file ends up with one status:
- expired: past its group's retention period;
- soon: expires within the soon window;
- kept: still inside its retention period, or kept forever;
- undated: in a group, but has none of the dates its group's age_date lists;
- unmatched: in no group.

Retention is counted from the first date the file has, in its group's age_date
order. A file kept 7 years from 2018-04-15 expires at the end of 2025-04-15.
"""

import csv
import json
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from bottrace import current_span, traced
from expirybot.survey import FileFacts, Survey
from expirybot.taxonomy import FOREVER, Placement, Taxonomy

STATUSES = ("expired", "soon", "kept", "undated", "unmatched")
DEFAULT_SOON_DAYS = 90


@dataclass(frozen=True)
class Flagged:
    file: FileFacts
    status: str
    placement: Placement | None = None
    age_source: str | None = None  # which date source was used
    age_date: date | None = None
    expires_on: date | None = None  # None when kept forever, undated or unmatched


@dataclass
class FlagResult:
    survey: Survey
    taxonomy: Taxonomy
    today: date
    soon_days: int
    files: list[Flagged] = field(default_factory=list)

    def with_status(self, status: str) -> list[Flagged]:
        return [f for f in self.files if f.status == status]

    def counts(self) -> Counter:
        return Counter(f.status for f in self.files)


@traced("flag", kind="stage")
def flag(survey: Survey, taxonomy: Taxonomy, today: date, soon_days: int = DEFAULT_SOON_DAYS):
    result = FlagResult(survey, taxonomy, today, soon_days)
    for file in survey.files:
        result.files.append(flag_file(file, taxonomy, today, soon_days))
    current_span().set(**{status: n for status, n in result.counts().items()})
    return result


def flag_file(file: FileFacts, taxonomy: Taxonomy, today: date, soon_days: int) -> Flagged:
    placement = taxonomy.place(file)
    if placement is None:
        return Flagged(file, "unmatched")
    source, age_date = next(
        ((s, file.date_from(s)) for s in placement.age_date if file.date_from(s)), (None, None)
    )
    if age_date is None:
        return Flagged(file, "undated", placement)
    if placement.keep_years == FOREVER:
        return Flagged(file, "kept", placement, source, age_date)
    expires_on = add_years(age_date, placement.keep_years)
    if expires_on < today:
        status = "expired"
    elif expires_on <= today + timedelta(days=soon_days):
        status = "soon"
    else:
        status = "kept"
    return Flagged(file, status, placement, source, age_date, expires_on)


def add_years(day: date, years: int) -> date:
    """The same calendar day, years later; 29 February becomes 28 February."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def expired_rows(result: FlagResult) -> list[dict]:
    """One row per expired file, for the expired list."""
    return [
        {
            "path": str(f.file.path),
            "group": f.placement.label,
            "age_date": f.age_date.isoformat(),
            "age_source": f.age_source,
            "expired_after": f.expires_on.isoformat(),
            "size_bytes": f.file.size_bytes,
        }
        for f in result.with_status("expired")
    ]


@traced("write expired list", kind="stage")
def write_expired_list(result: FlagResult, path: Path) -> int:
    """Write the expired files to a new .json or .csv file. Never writes over an
    existing file (raises FileExistsError). Returns the number of files listed."""
    rows = expired_rows(result)
    with path.open("x", encoding="utf-8", newline="") as f:  # "x" never overwrites
        if path.suffix.lower() == ".csv":
            writer = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["path"])
            writer.writeheader()
            writer.writerows(rows)
        else:
            json.dump(
                {
                    "folder": str(result.survey.root),
                    "taxonomy": str(result.taxonomy.source or result.taxonomy.name),
                    "as_of": result.today.isoformat(),
                    "files": rows,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
            f.write("\n")
    return len(rows)
