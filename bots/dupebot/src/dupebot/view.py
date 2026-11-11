"""Turn analysis results into a browser page (via the shared botview package).

The model only saw relative paths, so each file in its answer is matched back
to the full report entry for its size, dates and authors.
"""

from collections.abc import Callable

from botview import Action, Badge, Card, Page, Section, Table, Text
from dupebot.analyze import DELETION_CANDIDATE_ROLES, BatchResult, analysis_input
from dupebot.pricing import Cost, call_cost, usd

RELATIONSHIP_LABELS = {
    "copies": "Copies",
    "version_series": "Version series",
    "variants": "Variants",
    "recurring": "Recurring",
    "parts": "Parts of one document",
    "unrelated": "Unrelated",
}
ROLE_LABELS = {
    "current_version": "current version",
    "earlier_version": "earlier version",
    "original": "original",
    "copy": "copy",
    "member": "",
}
CONFIDENCE_TONES = {"high": "good", "medium": "info", "low": "caution"}

# Makes the "Show in Finder" button for a file, given its full path from the
# scan. Only the live browser app passes one; pages without it have no buttons.
Reveal = Callable[[str], Action]


def analysis_page(
    report: dict,
    results: list[BatchResult],
    model: str,
    effort: str,
    reveal: Reveal | None = None,
) -> Page:
    """effort is the human-readable effort, e.g. "model default (medium)"."""
    entries = _entries_by_relative_path(report)
    answers = {c["cluster_id"]: c for r in results for c in r.clusters}
    warnings = [w for r in results for w in r.warnings]
    candidates = sum(
        1
        for c in answers.values()
        for g in c["groups"]
        for f in g["files"]
        if f["role"] in DELETION_CANDIDATE_ROLES
    )
    summary = report["summary"]
    facts = [
        ("Folder", report["root"]),
        ("Model", model),
        ("Thinking effort", effort),
        ("Similarity threshold", str(report["settings"]["similarity_threshold"])),
        ("Clusters", str(summary["clusters"])),
        ("Files in clusters", str(summary["files_in_clusters"])),
        ("To consider deleting", str(candidates)),
        (
            "Tokens in / out",
            f"{sum(r.input_tokens for r in results):,} / {sum(r.output_tokens for r in results):,}",
        ),
        ("Estimated cost", estimated_cost(results, model)),
    ]
    notices = [
        Text(
            "These are suggestions from a language model that saw only file names, sizes, "
            "dates and authors, never contents. Highlighted files are ones to consider "
            "deleting; open and compare before you delete anything. DupeBot itself never "
            "deletes, moves or changes files.",
            tone="info",
        )
    ]
    if warnings:
        notices.append(
            Text(
                "The answer did not match the input exactly: " + "; ".join(warnings),
                tone="warning",
                label=f"{len(warnings)} problem(s)",
            )
        )
    sections = []
    for cluster in report["clusters"]:
        cid = cluster["cluster_id"]
        answer = answers.get(cid)
        if answer is None:
            sections.append(
                Section(cid, [Badge(cluster["extension"])], [Text("Not analysed.", "warning")])
            )
            continue
        n = sum(
            1 for g in answer["groups"] for f in g["files"] if f["role"] in DELETION_CANDIDATE_ROLES
        )
        badges = [Badge(cluster["extension"])]
        if n:
            badges.append(Badge(f"{n} to consider deleting", "caution"))
        cards = [_group_card(g, entries.get(cid, {}), reveal) for g in answer["groups"]]
        sections.append(Section(cid, badges, cards))
    return Page(
        title="DupeBot results",
        subtitle="Likely copies and older versions, grouped by the analyst model.",
        facts=facts,
        notices=notices,
        sections=sections,
    )


def _group_card(group: dict, entries: dict[str, dict], reveal: Reveal | None) -> Card:
    rows, tones = [], []
    for f in group["files"]:
        entry = entries.get(f["path"], {})
        row = [
            f["path"],
            ROLE_LABELS.get(f["role"], f["role"]),
            _size(entry.get("size_bytes")),
            (entry.get("modified") or "").replace("T", " "),
            entry.get("created_by"),
            entry.get("modified_by"),
        ]
        if reveal:
            # Buttons only for files from our own scan, never for paths the
            # model named that are not in it.
            row.append(reveal(entry["path"]) if "path" in entry else None)
        rows.append(row)
        if f["role"] in DELETION_CANDIDATE_ROLES:
            tones.append("caution")
        elif f["role"] in ("current_version", "original"):
            tones.append("good")
        else:
            tones.append(None)
    blocks = [
        Text(group["reason"]),
        Table(
            ["File", "Role", "Size", "Modified", "Created by", "Modified by"]
            + ([""] if reveal else []),
            rows,
            row_tones=tones,
            numeric=frozenset({2}),
        ),
    ]
    if group["check_before_deleting"].strip():
        blocks.append(
            Text(group["check_before_deleting"], tone="caution", label="Check before deleting")
        )
    confidence = group["confidence"]
    return Card(
        RELATIONSHIP_LABELS.get(group["relationship"], group["relationship"]),
        [Badge(f"{confidence} confidence", CONFIDENCE_TONES.get(confidence, "neutral"))],
        blocks,
    )


def estimated_cost(results: list[BatchResult], model: str) -> str:
    """Estimated cost of a run's calls, priced one call at a time, as text."""
    total = Cost()
    for r in results:
        cost = call_cost(model, r.input_tokens, r.output_tokens, r.thinking_tokens)
        if cost is None:
            return "unknown (no price for this model)"
        total += cost
    return usd(total.total)


def summary_lines(results: list[BatchResult]) -> list[str]:
    """A short plain-text summary, one line per cluster, for the terminal."""
    lines = []
    for r in results:
        for c in r.clusters:
            parts = []
            for g in c["groups"]:
                n = sum(1 for f in g["files"] if f["role"] in DELETION_CANDIDATE_ROLES)
                label = RELATIONSHIP_LABELS.get(g["relationship"], g["relationship"]).lower()
                extra = f", {n} to consider deleting" if n else ""
                parts.append(f"{label} ({len(g['files'])} files{extra}, {g['confidence']})")
            lines.append(f"{c['cluster_id']}: " + "; ".join(parts))
    return lines


def _entries_by_relative_path(report: dict) -> dict[str, dict[str, dict]]:
    """cluster_id -> {path as the model saw it -> full report entry}."""
    sent = analysis_input(report)["clusters"]
    return {
        full["cluster_id"]: {
            s["path"]: f for s, f in zip(trimmed["files"], full["files"], strict=True)
        }
        for full, trimmed in zip(report["clusters"], sent, strict=True)
    }


def _size(n: int | None) -> str:
    if n is None:
        return ""
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:,} {unit}" if unit == "bytes" else f"{n:,.1f} {unit}"
        n /= 1024
    return ""


def clusters_page(report: dict, reveal: Reveal | None = None) -> Page:
    """Stage 1 only: the clusters found, before anything is sent to a model."""
    summary = report["summary"]
    skipped = summary.get("skipped", {})
    facts = [
        ("Folder", report["root"]),
        ("Similarity threshold", str(report["settings"]["similarity_threshold"])),
        ("Candidate files", str(summary["candidate_files"])),
        ("Clusters", str(summary["clusters"])),
        ("Files in clusters", str(summary["files_in_clusters"])),
    ]
    facts += [(f"Skipped: {reason.replace('_', ' ')}", str(n)) for reason, n in skipped.items()]
    sent = analysis_input(report)["clusters"]
    sections = []
    for full, trimmed in zip(report["clusters"], sent, strict=True):
        rows = [
            [
                s["path"],
                f["cleaned_name"],
                _size(f["size_bytes"]),
                f["modified"].replace("T", " "),
                f["created_by"],
                f["modified_by"],
            ]
            + ([reveal(f["path"])] if reveal else [])
            for s, f in zip(trimmed["files"], full["files"], strict=True)
        ]
        table = Table(
            ["File", "Matched as", "Size", "Modified", "Created by", "Modified by"]
            + ([""] if reveal else []),
            rows,
            numeric=frozenset({2}),
        )
        badges = [Badge(full["extension"]), Badge(f"{len(rows)} files")]
        sections.append(Section(full["cluster_id"], badges, [table]))
    return Page(
        title="Clusters found",
        subtitle="Grouped by name only. Nothing has been sent to a model yet.",
        facts=facts,
        sections=sections,
    )
