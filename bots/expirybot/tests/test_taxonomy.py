import json
from pathlib import PurePosixPath

import pytest

from expirybot.survey import survey
from expirybot.taxonomy import DEFAULT_AGE_DATE, TaxonomyError, load_taxonomy, parse_taxonomy

TAXONOMY = {
    "name": "Test",
    "groups": [
        {
            "name": "Tax",
            "match": {"folders": ["tax"]},
            "keep_years": 7,
            "age_date": ["modified"],
            "subgroups": [
                {"name": "Returns", "match": {"words": ["1040"]}, "keep_years": "forever"}
            ],
        },
        {"name": "Bills", "match": {"words": ["bill"], "extensions": ["PDF"]}, "keep_years": 2},
    ],
}


def placed(taxonomy, relpath):
    class F:
        pass

    f = F()
    f.relpath = PurePosixPath(relpath)
    return taxonomy.place(f)


def test_places_files_in_the_first_matching_group_and_subgroup():
    t = parse_taxonomy(TAXONOMY)
    assert placed(t, "Taxes/2021/W-2.pdf").label == "Tax"
    assert placed(t, "Taxes/2021/Form 1040.pdf").label == "Tax / Returns"
    assert placed(t, "Taxes/electric bill.pdf").label == "Tax"  # first group wins
    assert placed(t, "Home/Electric Bill.pdf").label == "Bills"
    assert placed(t, "Home/Electric Bill.docx") is None  # every rule must hold
    assert placed(t, "Syntax/notes.txt").label == "Tax"  # folder terms match anywhere


def test_subgroups_inherit_what_they_leave_out():
    t = parse_taxonomy(TAXONOMY)
    returns = placed(t, "Tax/1040.pdf")
    assert returns.keep_years == "forever"
    assert returns.age_date == ("modified",)  # from Tax
    assert placed(t, "bill.pdf").age_date == DEFAULT_AGE_DATE


def test_lists_every_problem():
    bad = {
        "groups": [
            {"name": "A", "match": {}, "keep_years": 0, "age_date": ["mtime"], "colour": "red"},
            {"match": {"words": ["x"]}},
            {"name": "B", "match": {"words": ["b"]}, "keep_years": 1},
            {"name": "B", "match": {"words": ["c"]}, "keep_years": 1},
        ]
    }
    with pytest.raises(TaxonomyError) as e:
        parse_taxonomy(bad)
    text = " | ".join(e.value.problems)
    for expected in (
        "unknown key(s) ['colour']",
        "keep_years must be a whole number",
        "unknown date source(s) ['mtime']",
        "needs at least one match rule",
        "groups[1] needs a name",
        "needs keep_years",
        'two groups named "B"',
    ):
        assert expected in text


def test_load_reports_unreadable_and_invalid_files(tmp_path):
    with pytest.raises(TaxonomyError, match="cannot read"):
        load_taxonomy(tmp_path / "missing.json")
    broken = tmp_path / "broken.json"
    broken.write_text("{")
    with pytest.raises(TaxonomyError, match="not valid JSON"):
        load_taxonomy(broken)


def test_example_taxonomy_loads(tmp_path, make_file):
    from pathlib import Path

    example = Path(__file__).parents[1] / "examples" / "taxonomy.json"
    t = load_taxonomy(example)
    make_file(tmp_path, "Payroll/earnings statement 2024-01.pdf")
    (f,) = survey(tmp_path).files
    assert t.place(f).label == "Pay stubs"  # not caught by the statements group


def test_load_keeps_the_source(tmp_path):
    path = tmp_path / "t.json"
    path.write_text(json.dumps(TAXONOMY))
    assert load_taxonomy(path).source == path
