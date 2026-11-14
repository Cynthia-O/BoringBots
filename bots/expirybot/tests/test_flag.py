import csv
import json
from datetime import date

import pytest

from expirybot.flag import add_years, flag, write_expired_list
from expirybot.survey import survey
from expirybot.taxonomy import parse_taxonomy

TODAY = date(2026, 11, 13)
TAXONOMY = parse_taxonomy(
    {
        "groups": [
            {
                "name": "Tax",
                "match": {"folders": ["tax"]},
                "keep_years": 7,
                "subgroups": [
                    {"name": "Returns", "match": {"words": ["1040"]}, "keep_years": "forever"}
                ],
            },
            {
                "name": "Statements",
                "match": {"words": ["statement"]},
                "keep_years": 1,
                "age_date": ["name"],
            },
        ]
    }
)


def statuses(tmp_path, taxonomy=TAXONOMY, soon_days=90):
    result = flag(survey(tmp_path), taxonomy, TODAY, soon_days)
    return {str(f.file.relpath): f for f in result.files}


def test_each_status(tmp_path, make_file):
    make_file(tmp_path, "Tax/old W-2.pdf", modified="2019-01-01")  # expired 2026-01-01
    make_file(tmp_path, "Tax/recent W-2.pdf", modified="2020-01-01")  # expires 2027-01-01
    make_file(tmp_path, "Tax/W-2 later.pdf", modified="2023-01-01")
    make_file(tmp_path, "Tax/1040 1990.pdf", modified="1995-01-01")
    make_file(tmp_path, "bank statement.pdf")  # no date in its name
    make_file(tmp_path, "holiday.jpg")
    got = statuses(tmp_path)
    assert got["Tax/old W-2.pdf"].status == "expired"
    assert got["Tax/old W-2.pdf"].expires_on == date(2026, 1, 1)
    assert got["Tax/recent W-2.pdf"].status == "soon"
    assert got["Tax/W-2 later.pdf"].status == "kept"
    assert got["Tax/1040 1990.pdf"].status == "kept"  # forever
    assert got["Tax/1040 1990.pdf"].expires_on is None
    assert got["bank statement.pdf"].status == "undated"
    assert got["holiday.jpg"].status == "unmatched"


def test_first_date_source_a_file_has_is_used(tmp_path, make_file):
    taxonomy = parse_taxonomy(
        {"groups": [{"name": "S", "match": {"words": ["stmt"]}, "keep_years": 1,
                     "age_date": ["name", "modified"]}]}
    )  # fmt: skip
    make_file(tmp_path, "stmt 2025-03.pdf", modified="2026-01-01")
    make_file(tmp_path, "stmt.pdf", modified="2025-01-01")
    got = statuses(tmp_path, taxonomy)
    assert got["stmt 2025-03.pdf"].age_source == "name"
    assert got["stmt 2025-03.pdf"].age_date == date(2025, 3, 31)
    assert got["stmt.pdf"].age_source == "modified"


def test_expires_at_the_end_of_the_last_day(tmp_path, make_file):
    make_file(tmp_path, "Tax/a.pdf", modified="2019-11-13")  # expires 2026-11-13: not yet
    make_file(tmp_path, "Tax/b.pdf", modified="2019-11-12")
    got = statuses(tmp_path, soon_days=0)
    assert got["Tax/a.pdf"].status == "soon"
    assert got["Tax/b.pdf"].status == "expired"


def test_add_years_handles_leap_days():
    assert add_years(date(2024, 2, 29), 1) == date(2025, 2, 28)
    assert add_years(date(2024, 2, 29), 4) == date(2028, 2, 29)


@pytest.mark.parametrize("suffix", [".json", ".csv"])
def test_expired_list_is_a_new_file(tmp_path, suffix, make_file):
    root = tmp_path / "docs"
    make_file(root, "Tax/old.pdf", modified="2010-01-01")
    make_file(root, "Tax/new.pdf", modified="2026-01-01")
    result = flag(survey(root), TAXONOMY, TODAY)
    out = tmp_path / f"expired{suffix}"
    assert write_expired_list(result, out) == 1
    text = out.read_text()
    if suffix == ".json":
        rows = json.loads(text)["files"]
    else:
        rows = list(csv.DictReader(text.splitlines()))
    assert [r["path"] for r in rows] == [str(root.resolve() / "Tax/old.pdf")]
    assert rows[0]["expired_after"] == "2017-01-01"
    with pytest.raises(FileExistsError):
        write_expired_list(result, out)
    assert out.read_text() == text
