from datetime import date
from pathlib import PurePosixPath

from expirybot.survey import hidden_folder, survey


def test_records_dates_from_every_source(tmp_path, make_file):
    make_file(tmp_path, "Taxes/2021/W-2 2021.pdf", modified="2022-02-10")
    s = survey(tmp_path)
    (f,) = s.files
    assert f.relpath == PurePosixPath("Taxes/2021/W-2 2021.pdf")
    assert f.modified == date(2022, 2, 10)
    assert f.date_from("name") == date(2021, 12, 31)
    assert f.date_from("folder") == date(2021, 12, 31)
    assert f.date_from("inside_modified") is None  # not a real PDF
    assert f.created is not None  # macOS and most filesystems record it
    assert f.evidence("name") == '"2021" in the name'


def test_nearest_dated_folder_wins(tmp_path, make_file):
    make_file(tmp_path, "2019/Statements 2023-03/bank.pdf")
    (f,) = survey(tmp_path).files
    assert f.date_from("folder") == date(2023, 3, 31)


def test_skips_clutter_but_surveys_hidden_folders(tmp_path, make_file):
    make_file(tmp_path, "keep.txt")
    make_file(tmp_path, ".DS_Store")
    make_file(tmp_path, "app/.git/objects/ab/cd")
    s = survey(tmp_path)
    assert sorted(str(f.relpath) for f in s.files) == ["app/.git/objects/ab/cd", "keep.txt"]
    assert s.skipped["clutter"] == 1


def test_hidden_folder_is_the_outermost_one():
    assert hidden_folder(PurePosixPath("app/.git/objects/.x/cd")) == PurePosixPath("app/.git")
    assert hidden_folder(PurePosixPath(".zshrc")) is None  # a hidden file, not in a folder
    assert hidden_folder(PurePosixPath("Taxes/w2.pdf")) is None


def test_does_not_follow_symlinks(tmp_path, make_file):
    target = make_file(tmp_path / "outside", "secret.txt")
    (tmp_path / "scan").mkdir()
    (tmp_path / "scan" / "link.txt").symlink_to(target)
    assert survey(tmp_path / "scan").files == []
