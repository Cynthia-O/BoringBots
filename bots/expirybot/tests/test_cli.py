import json

import pytest

from bottrace import read_traces
from expirybot.cli import main, pages_dir

TAXONOMY = {"groups": [{"name": "Tax", "match": {"folders": ["tax"]}, "keep_years": 7}]}


def write_taxonomy(tmp_path, data=TAXONOMY):
    path = tmp_path / "taxonomy.json"
    path.write_text(json.dumps(data))
    return path


def test_survey_without_a_taxonomy(tmp_path, capsys, make_file):
    root = tmp_path / "docs"
    make_file(root, "Tax/w2.pdf")
    assert main([str(root)]) == 0
    assert "Surveyed 1 files" in capsys.readouterr().err
    (page,) = pages_dir().glob("*expirybot-survey.html")
    assert "Date sources" in page.read_text()


def test_flags_against_a_taxonomy(tmp_path, capsys, make_file):
    root = tmp_path / "docs"
    make_file(root, "Tax/old.pdf", modified="2010-01-01")
    make_file(root, "photo.jpg")
    out = tmp_path / "expired.csv"
    args = [str(root), "--taxonomy", str(write_taxonomy(tmp_path)), "--today", "2026-11-13"]
    assert main([*args, "--expired-list", str(out)]) == 0
    err = capsys.readouterr().err
    assert "1 expired" in err and "1 in no group" in err
    assert "old.pdf" in out.read_text()
    (page,) = pages_dir().glob("*expirybot-flags.html")
    assert "Tax/old.pdf" in page.read_text()


def test_traces_the_run(tmp_path, make_file):
    root = tmp_path / "docs"
    make_file(root, "a.txt")
    main([str(root), "--taxonomy", str(write_taxonomy(tmp_path))])
    (spans,) = read_traces()
    assert {"command line", "survey folder", "flag", "write page"} <= {s["name"] for s in spans}


def test_reports_taxonomy_problems(tmp_path, capsys):
    root = tmp_path / "docs"
    root.mkdir()
    bad = write_taxonomy(tmp_path, {"groups": [{"name": "X", "match": {"words": ["x"]}}]})
    assert main([str(root), "--taxonomy", str(bad)]) == 2
    assert "needs keep_years" in capsys.readouterr().err


def test_refuses_to_overwrite_the_expired_list(tmp_path):
    root = tmp_path / "docs"
    root.mkdir()
    out = tmp_path / "expired.json"
    out.write_text("mine")
    with pytest.raises(SystemExit):
        main([str(root), "--taxonomy", str(write_taxonomy(tmp_path)), "--expired-list", str(out)])
    assert out.read_text() == "mine"


def test_rejects_missing_directory(tmp_path):
    with pytest.raises(SystemExit):
        main([str(tmp_path / "nope")])


def test_pages_list_hidden_folders_and_where_their_files_landed(tmp_path, make_file):
    root = tmp_path / "docs"
    make_file(root, "app/.git/objects/ab/cd")
    make_file(root, "app/.git/HEAD")
    make_file(root, ".private/old taxes.pdf")
    taxonomy = {
        "groups": [{"name": "Repos", "match": {"folders": [".git"]}, "keep_years": "forever"}]
    }
    main([str(root), "--taxonomy", str(write_taxonomy(tmp_path, taxonomy))])
    (page,) = pages_dir().glob("*expirybot-flags.html")
    text = page.read_text()
    assert "app/.git/" in text and "Repos (2)" in text
    assert ".private/" in text and "not in any group (1)" in text


def test_folders_chart_pages_folders_and_folds_rare_types_into_other(tmp_path, make_file):
    from expirybot.survey import survey
    from expirybot.view import _type_categories, _type_counts, survey_page

    root = tmp_path / "docs"
    for i in range(12):  # 12 folders: two pages of 10
        for j in range(i + 1):
            make_file(root, f"folder{i:02d}/file{j}.pdf")
    for ext in (".docx", ".xlsx", ".jpg", ".png", ".txt", ".csv", ".zip"):
        make_file(root, f"misc/file{ext}")
    s = survey(root)
    categories = _type_categories(s.files)
    assert categories[0] == ".pdf" and categories[-1] == "Other" and len(categories) == 7
    assert sum(_type_counts(s.files, categories)) == len(s.files)  # adds up to 100%

    from botview import render

    text = render(survey_page(s))
    assert "Folders at a glance" in text and "Folders 1–10 of 13" in text
    assert text.index("folder11/") < text.index("folder10/")  # most files first
    assert "File types" in text and ".pdf 100%" in text  # the table's text view
    assert "File types</h2>" not in text  # the old list section is gone
