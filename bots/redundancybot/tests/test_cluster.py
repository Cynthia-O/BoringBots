from pathlib import Path

from redundancybot.cluster import group_by_extension, scan


def test_group_by_extension_drops_singletons_and_ignores_case():
    paths = [Path("a.PDF"), Path("b.pdf"), Path("c.txt"), Path("README"), Path("LICENSE")]
    groups = group_by_extension(paths)
    assert groups == {
        ".pdf": [Path("a.PDF"), Path("b.pdf")],
        "": [Path("README"), Path("LICENSE")],
    }


def test_scan_finds_nested_files(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "x.txt").write_text("x")
    (tmp_path / "sub" / "y.txt").write_text("y")
    assert sorted(p.name for p in scan(tmp_path)) == ["x.txt", "y.txt"]
