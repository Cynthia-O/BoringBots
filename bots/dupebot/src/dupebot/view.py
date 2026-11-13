"""Turn analysis results into a browser page (via the shared botview package).

The model only saw relative paths, so each file in its answer is matched back
to the full report entry for its size, dates and authors.
"""

from collections.abc import Callable, Sequence

from botview import Action, Badge, Block, Card, Page, Section, Table, Text
from dupebot.analyze import (
    DELETION_CANDIDATE_ROLES,
    BatchResult,
    analysis_input,
    relative_entries,
)
from dupebot.suggest import Suggestion, SuggestResult
from finops.pricing import Cost, call_cost, usd

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

# Makes the "Show in Finder" button for a file, given its full path from the
# scan. Only the live browser app passes one; pages without it have no buttons.
Reveal = Callable[[str], Action]


def results_page(
    report: dict,
    answers: dict[str, dict],
    model: str,
    effort: str,
    reveal: Reveal | None = None,
    suggestions: SuggestResult | None = None,
    agent_note: str | None = None,
    cost: str | None = None,
    warnings: Sequence[str] = (),
    live: bool = False,
    timing: Sequence[Block] = (),
) -> Page:
    """The one results page, drawn again as a run progresses.

    answers maps cluster_id to the analysis answer for each cluster that has
    arrived so far; the rest show as waiting, so the page fills in from the top
    down. agent_note says what the prioritization agent is doing. Once its
    suggestions are given, the clusters it picked move to the top, in its order,
    with all other clusters below. effort is the human-readable effort.

    live: shown inside the browser app, whose stage tracker already says what the
    agent is doing and that these are only suggestions, so those notices are left
    out. Saved copies have no tracker and keep them.
    """
    entries = relative_entries(report)
    clusters = report["clusters"]
    done = suggestions is not None
    candidates = sum(
        1
        for a in answers.values()
        for g in a["groups"]
        for f in g["files"]
        if f["role"] in DELETION_CANDIDATE_ROLES
    )
    facts = [
        ("Folder", report["root"]),
        ("Model", model),
        ("Thinking effort", effort),
        ("Similarity threshold", str(report["settings"]["similarity_threshold"])),
        ("Clusters answered", f"{len(answers)} of {len(clusters)}"),
        ("To consider deleting", str(candidates)),
    ]
    if done:
        facts.append(("Prioritized", f"{len(suggestions.suggestions)} (limit {suggestions.cap})"))
    if cost:
        facts.append(("Estimated cost", cost))

    notices = []
    if not live:
        notices.append(
            Text(
                "These are suggestions from a language model that saw only file names, sizes, "
                "dates and authors, plus the checks listed below. Open and compare before you "
                "delete anything. DupeBot itself never deletes, moves or changes files.",
                tone="info",
            )
        )
        if agent_note:
            notices.append(Text(agent_note, tone="info", label="Prioritization agent"))
        elif done and suggestions.summary:
            notices.append(Text(suggestions.summary, tone="good", label="Prioritization agent"))
    if warnings:
        notices.append(
            Text(" ".join(warnings), tone="warning", label=f"{len(warnings)} problem(s)")
        )

    picked: dict[str, list[Suggestion]] = {}
    for s in suggestions.suggestions if done else []:
        picked.setdefault(s.cluster_id, []).append(s)
    by_id = {c["cluster_id"]: c for c in clusters}
    sections = []
    rank = 0
    for cid, items in picked.items():  # the agent's order: its safest pick first
        rank += 1
        cards = [_suggestion_card(s, entries.get(cid, {}), reveal) for s in items]
        sections.append(
            _cluster_section(
                by_id[cid],
                answers.get(cid),
                entries.get(cid, {}),
                reveal,
                extra=cards,
                priority=rank,
            )
        )
    rest = [c for c in clusters if c["cluster_id"] not in picked]
    if picked and rest:
        sections.append(
            Section(
                "Other clusters",
                [Badge(f"{len(rest)} clusters")],
                [
                    Text(
                        "Everything else the analysis found, in scan order. Check here for "
                        "anything the agent passed over."
                    )
                ],
            )
        )
    for cluster in rest:
        cid = cluster["cluster_id"]
        sections.append(_cluster_section(cluster, answers.get(cid), entries.get(cid, {}), reveal))
    if done and suggestions.checks:
        rows = [[c.tool.replace("_", " "), c.target, c.outcome] for c in suggestions.checks]
        tones = ["warning" if c.is_error else None for c in suggestions.checks]
        sections.append(
            Section(
                "What the prioritization agent checked",
                [Badge("read-only")],
                [
                    Text(
                        "The agent chose these checks itself. File comparisons ran on this "
                        "Mac; only the result was sent to the model."
                    ),
                    Table(["Check", "On", "Result"], rows, row_tones=tones),
                ],
            )
        )
    if timing:
        sections.append(Section("How long this took", [Badge("this request")], list(timing)))
    return Page(
        title="DupeBot results",
        subtitle="Likely copies and older versions, with the agent's picks first.",
        facts=facts,
        notices=notices,
        sections=sections,
        searchable=done,  # redrawn while streaming, which would reset a filter
    )


def _cluster_section(
    cluster: dict,
    answer: dict | None,
    entries: dict[str, dict],
    reveal: Reveal | None,
    extra: Sequence[Card] = (),
    priority: int | None = None,
) -> Section:
    badges = []
    if priority is not None:
        badges.append(Badge(f"priority {priority}", "info"))
    badges.append(Badge(cluster["extension"]))
    if answer is None:
        badges.append(Badge("waiting for the model…"))
        n = len(cluster["files"])
        return Section(cluster["cluster_id"], badges, [Text(f"{n} files, not answered yet.")])
    n = sum(
        1 for g in answer["groups"] for f in g["files"] if f["role"] in DELETION_CANDIDATE_ROLES
    )
    if n:
        badges.append(Badge(f"{n} to consider deleting", "caution"))
    cards = [_group_card(g, entries, reveal) for g in answer["groups"]]
    return Section(cluster["cluster_id"], badges, [*extra, *cards])


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
    return Card(RELATIONSHIP_LABELS.get(group["relationship"], group["relationship"]), [], blocks)


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
                parts.append(f"{label} ({len(g['files'])} files{extra})")
            lines.append(f"{c['cluster_id']}: " + "; ".join(parts))
    return lines


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


def _suggestion_card(s: Suggestion, entries: dict[str, dict], reveal: Reveal | None) -> Card:
    """One of the agent's picks: the file to consider deleting and the one to keep."""
    badges = []
    if s.identical is True:
        badges.append(Badge("identical contents (checked)", "good"))
    elif s.identical is False:
        badges.append(Badge("contents differ (checked)", "neutral"))
    rows, tones = [], []
    for role, rel, tone in (("Consider deleting", s.delete, "caution"), ("Keep", s.keep, "good")):
        entry = entries.get(rel, {})
        row = [
            role,
            rel,
            _size(entry.get("size_bytes")),
            (entry.get("modified") or "").replace("T", " "),
        ]
        if reveal:
            row.append(reveal(entry["path"]) if "path" in entry else None)
        rows.append(row)
        tones.append(tone)
    table = Table(
        ["", "File", "Size", "Modified"] + ([""] if reveal else []),
        rows,
        row_tones=tones,
        numeric=frozenset({2}),
    )
    title = f"Suggested: delete {s.delete.rsplit('/', 1)[-1]}"
    return Card(title, badges, [Text(s.reason), table])
