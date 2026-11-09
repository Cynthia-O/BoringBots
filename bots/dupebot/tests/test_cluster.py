import os
from pathlib import Path

import pytest

from dupebot.cleaning import clean_name
from dupebot.cluster import (
    FileRecord,
    bucket_by_prefix,
    cluster_names,
    cluster_records,
    group_by_extension,
    jaccard,
    scan,
    trigrams,
)


def record(filename: str, modified: float = 0.0) -> FileRecord:
    return FileRecord(Path(filename), 300_000, modified, clean_name(filename))


def test_group_by_extension_drops_singletons_and_ignores_case():
    records = [record("a.PDF"), record("b.pdf"), record("c.txt"), record("x"), record("y")]
    groups = group_by_extension(records)
    assert {ext: [r.path.name for r in rs] for ext, rs in groups.items()} == {
        ".pdf": ["a.PDF", "b.pdf"],
        "": ["x", "y"],
    }


def test_scan_finds_nested_files_and_records_skips(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "budget.txt").write_bytes(b"x" * 2000)
    (tmp_path / "sub" / "plan.txt").write_bytes(b"x" * 2000)
    (tmp_path / "sub" / "tiny.txt").write_bytes(b"x")
    (tmp_path / "index.html").write_bytes(b"x" * 2000)
    (tmp_path / "FINAL_2024-03-01.txt").write_bytes(b"x" * 2000)
    os.symlink(tmp_path / "budget.txt", tmp_path / "link.txt")

    result = scan(tmp_path, min_size=1000)

    assert sorted(r.path.name for r in result.records) == ["budget.txt", "plan.txt"]
    assert {k: [p.name for p in v] for k, v in result.skipped.items()} == {
        "too_small": ["tiny.txt"],
        "excluded": ["index.html"],
        "empty_after_cleaning": ["FINAL_2024-03-01.txt"],
    }


def test_bucket_by_prefix_lengthens_prefix_until_buckets_fit():
    records = [record(f"report{c}{i}.txt") for c in "ab" for i in range(3)]
    records.append(record("other.txt"))
    buckets = bucket_by_prefix(records, length=5, max_size=3)
    assert sorted(sorted(r.name.cleaned for r in b) for b in buckets) == [
        ["other"],
        ["reporta0", "reporta1", "reporta2"],
        ["reportb0", "reportb1", "reportb2"],
    ]


def test_bucket_by_prefix_stops_when_names_cannot_be_split():
    records = [record("same.txt") for _ in range(5)]
    assert len(bucket_by_prefix(records, length=2, max_size=3)) == 1


def test_trigrams_and_jaccard():
    assert trigrams("abcd") == {"abc", "bcd"}
    assert trigrams("ab") == {"ab"}
    assert jaccard(trigrams("abcd"), trigrams("abcd")) == 1.0
    assert jaccard(trigrams("abcd"), trigrams("bcde")) == pytest.approx(1 / 3)


def test_cluster_names_links_chains_and_drops_singletons():
    names = ["budget review", "budget reviews", "budget reviewed", "holiday photos"]
    assert cluster_names(names, 0.6) == [[0, 1, 2]]


def test_cluster_records_end_to_end():
    records = [
        record("Budget Review_v2.xlsx", modified=2),
        record("FINAL_Budget Review 2023-01-15.xlsx", modified=3),
        record("budget_review_DE.xlsx", modified=1),
        record("Budget Review.pdf"),  # different extension: never clustered with xlsx
        record("Travel Plans.xlsx"),
    ]
    clusters = cluster_records(records, threshold=0.6, workers=1)
    assert len(clusters) == 1
    assert clusters[0].extension == ".xlsx"
    assert [r.path.name for r in clusters[0].files] == [  # oldest first
        "budget_review_DE.xlsx",
        "Budget Review_v2.xlsx",
        "FINAL_Budget Review 2023-01-15.xlsx",
    ]


def test_cluster_records_parallel_matches_serial(monkeypatch):
    monkeypatch.setattr("dupebot.cluster.PARALLEL_MIN_COMPARISONS", 0)
    records = [record(f"{stem} {suffix}.txt") for stem in ("alpha", "bravo", "charl")
               for suffix in ("notes", "notez", "nodes")]  # fmt: skip
    serial = cluster_records(records, workers=1)
    parallel = cluster_records(records, workers=2)
    assert serial == parallel
    assert len(serial) == 3


def test_cluster_records_rejects_bad_threshold():
    with pytest.raises(ValueError):
        cluster_records([], threshold=0)
