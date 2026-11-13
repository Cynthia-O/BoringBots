"""Turn stage 1 clusters into a JSON-ready report for the LLM stages and the user."""

from datetime import datetime
from pathlib import Path

from bottrace import traced
from dupebot.cluster import (
    MAX_BUCKET_SIZE,
    MIN_SIZE_IMAGE_BYTES,
    MIN_SIZE_OTHER_BYTES,
    PREFIX_START,
    Cluster,
    FileRecord,
    ScanResult,
)
from dupebot.metadata import read_authors


@traced("build report", kind="stage")
def build_report(
    root: Path,
    scan_result: ScanResult,
    clusters: list[Cluster],
    threshold: float,
) -> dict:
    per_extension: dict[str, int] = {}
    cluster_entries = []
    for cluster in clusters:
        n = per_extension[cluster.extension] = per_extension.get(cluster.extension, 0) + 1
        cluster_entries.append(
            {
                "cluster_id": f"{cluster.extension.lstrip('.') or 'noext'}-{n:04d}",
                "extension": cluster.extension,
                "files": [_file_entry(r) for r in cluster.files],
            }
        )
    return {
        "root": str(root),
        "settings": {
            "similarity_threshold": threshold,
            "min_size_bytes": {"images": MIN_SIZE_IMAGE_BYTES, "other": MIN_SIZE_OTHER_BYTES},
            "prefix_start": PREFIX_START,
            "max_bucket_size": MAX_BUCKET_SIZE,
        },
        "summary": {
            "candidate_files": len(scan_result.records),
            "clusters": len(clusters),
            "files_in_clusters": sum(len(c.files) for c in clusters),
            "skipped": {
                reason: len(paths) for reason, paths in sorted(scan_result.skipped.items())
            },
        },
        "clusters": cluster_entries,
        "empty_after_cleaning": [
            str(p) for p in scan_result.skipped.get("empty_after_cleaning", [])
        ],
    }


def _file_entry(record: FileRecord) -> dict:
    # Authors are read here, for clustered files only, because it means opening
    # the file; doing it during the scan would open every file in the folder.
    name = record.name
    authors = read_authors(record.path)
    return {
        "path": str(record.path),
        "name": name.original,
        "cleaned_name": name.cleaned,
        "size_bytes": record.size_bytes,
        "modified": datetime.fromtimestamp(record.modified).isoformat(timespec="seconds"),
        "created_by": authors.created_by,
        "modified_by": authors.modified_by,
        "dates": list(name.dates),
        "version_markers": list(name.version_markers),
        "set_markers": list(name.set_markers),
    }
