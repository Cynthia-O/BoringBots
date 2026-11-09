"""Stage 1: deterministic clustering. No LLM involved.

Pipeline:
1. Scan: list regular files under root (symlinks are skipped), with size and
   modified time. Read-only.
2. Filter: skip excluded names (see ``cleaning``), files under MIN_SIZE_BYTES,
   and names that are empty after cleaning.
3. Group by extension, dropping extensions with only one remaining file.
4. Bucket by prefix of the cleaned name: first PREFIX_START characters, then one
   more character at a time for any bucket over MAX_BUCKET_SIZE files.
5. Cluster within each bucket: files whose cleaned names have a Jaccard similarity
   of character trigrams at or above the threshold are linked, and each connected
   group of linked files is a cluster. Each bucket runs whole on one core, with
   buckets spread across one core fewer than the machine has.
"""

import os
import stat
from collections import defaultdict
from collections.abc import Iterable, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from itertools import repeat
from pathlib import Path

from dupebot.cleaning import CleanedName, clean_name, exclusion_reason

MIN_SIZE_BYTES = 250 * 1024
DEFAULT_THRESHOLD = 0.6
PREFIX_START = 5
MAX_BUCKET_SIZE = 1000
# Below this many pairwise comparisons in total, starting worker processes costs
# more time than it saves, so clustering runs in this process.
PARALLEL_MIN_COMPARISONS = 200_000


@dataclass(frozen=True)
class FileRecord:
    path: Path
    size_bytes: int
    modified: float  # POSIX timestamp
    name: CleanedName


@dataclass
class ScanResult:
    records: list[FileRecord] = field(default_factory=list)
    skipped: dict[str, list[Path]] = field(default_factory=lambda: defaultdict(list))


@dataclass(frozen=True)
class Cluster:
    extension: str
    files: tuple[FileRecord, ...]  # oldest modified first


def scan(root: Path, min_size: int = MIN_SIZE_BYTES) -> ScanResult:
    """Find candidate files under root, recording why the others were skipped."""
    result = ScanResult()
    for dirpath, _dirnames, filenames in os.walk(root):  # does not follow symlinks
        for filename in filenames:
            path = Path(dirpath) / filename
            try:
                info = path.lstat()
            except OSError:
                result.skipped["unreadable"].append(path)
                continue
            if not stat.S_ISREG(info.st_mode):
                continue  # symlinks, sockets and the like
            name = clean_name(filename)
            reason = exclusion_reason(name)
            if reason is None and info.st_size < min_size:
                reason = "too_small"
            if reason is None and not name.cleaned:
                reason = "empty_after_cleaning"
            if reason:
                result.skipped[reason].append(path)
            else:
                result.records.append(FileRecord(path, info.st_size, info.st_mtime, name))
    return result


def group_by_extension(records: Iterable[FileRecord]) -> dict[str, list[FileRecord]]:
    """Map lowercase extension -> records, dropping extensions with only one file.

    Files with no extension are grouped under the empty string.
    """
    groups: dict[str, list[FileRecord]] = defaultdict(list)
    for record in records:
        groups[record.path.suffix.lower()].append(record)
    return {ext: files for ext, files in groups.items() if len(files) > 1}


def bucket_by_prefix(
    records: Sequence[FileRecord],
    length: int = PREFIX_START,
    max_size: int = MAX_BUCKET_SIZE,
) -> list[list[FileRecord]]:
    """Split records into buckets that share the first ``length`` cleaned characters.

    Any bucket over max_size is split again on one more character, until it fits
    or its names cannot be told apart by prefix (for example, all identical).
    """
    buckets: dict[str, list[FileRecord]] = defaultdict(list)
    for record in records:
        buckets[record.name.cleaned[:length]].append(record)
    result = []
    for bucket in buckets.values():
        if len(bucket) > max_size and any(len(r.name.cleaned) > length for r in bucket):
            result.extend(bucket_by_prefix(bucket, length + 1, max_size))
        else:
            result.append(bucket)
    return result


def trigrams(text: str) -> frozenset[str]:
    """Character trigrams of text. Text shorter than 3 characters is its own set."""
    if len(text) < 3:
        return frozenset({text})
    return frozenset(text[i : i + 3] for i in range(len(text) - 2))


def jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    union = len(a | b)
    return len(a & b) / union if union else 0.0


def cluster_names(names: Sequence[str], threshold: float) -> list[list[int]]:
    """Cluster names by trigram Jaccard similarity.

    Returns groups of indices into names, each with at least two members. Two
    names are in the same group if they are linked by a chain of pairs whose
    similarity is at or above threshold.
    """
    grams = [trigrams(n) for n in names]
    parent = list(range(len(names)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            if jaccard(grams[i], grams[j]) >= threshold:
                parent[find(j)] = find(i)

    groups: dict[int, list[int]] = defaultdict(list)
    for i in range(len(names)):
        groups[find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def worker_count() -> int:
    """Cores this process may use, minus one so the machine stays responsive."""
    count = getattr(os, "process_cpu_count", os.cpu_count)() or 1
    return max(1, count - 1)


def cluster_records(
    records: Iterable[FileRecord],
    threshold: float = DEFAULT_THRESHOLD,
    workers: int | None = None,
    prefix_start: int = PREFIX_START,
    max_bucket_size: int = MAX_BUCKET_SIZE,
) -> list[Cluster]:
    """Run steps 3 to 5 of the pipeline on already-filtered records."""
    if not 0 < threshold <= 1:
        raise ValueError(f"threshold must be above 0 and at most 1, got {threshold}")

    jobs: list[tuple[str, list[FileRecord]]] = []
    for ext, files in group_by_extension(records).items():
        for bucket in bucket_by_prefix(files, prefix_start, max_bucket_size):
            if len(bucket) > 1:
                jobs.append((ext, bucket))
    jobs.sort(key=lambda job: len(job[1]), reverse=True)  # largest first balances cores
    names = [[r.name.cleaned for r in bucket] for _, bucket in jobs]

    workers = worker_count() if workers is None else workers
    comparisons = sum(len(n) * (len(n) - 1) // 2 for n in names)
    if workers > 1 and len(jobs) > 1 and comparisons >= PARALLEL_MIN_COMPARISONS:
        with ProcessPoolExecutor(max_workers=min(workers, len(jobs))) as pool:
            results = list(pool.map(cluster_names, names, repeat(threshold)))
    else:
        results = [cluster_names(n, threshold) for n in names]

    clusters = []
    for (ext, bucket), groups in zip(jobs, results, strict=True):
        for group in groups:
            files = sorted((bucket[i] for i in group), key=lambda r: (r.modified, str(r.path)))
            clusters.append(Cluster(ext, tuple(files)))
    clusters.sort(key=lambda c: (c.extension, c.files[0].name.cleaned, str(c.files[0].path)))
    return clusters
