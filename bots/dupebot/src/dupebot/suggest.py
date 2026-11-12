"""Stage 2b: an agent picks the clean-ups most worth the person's attention.

The analysis stage labels every clustered file. This stage reads those labels
and investigates with two read-only tools before suggesting specific files to
delete, each paired with the file to keep:

- compare_files: are two clustered files byte-for-byte identical? Hashed on this
  machine; only the yes/no answer (and the sizes) goes to the API.
- list_folder: names, sizes and dates in a folder inside the scanned folder.

The model decides which tools to call and when it has seen enough, within a cap
on turns. Its final answer has a fixed shape (structured outputs). Python then
checks every suggestion against the scan before showing it, and records whether
a file was really found identical from its own tool results, not from what the
model says.

Nothing here can change a file: the tools only read, and DupeBot never deletes.
"""

import hashlib
import json
import math
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import anthropic

from dupebot.analyze import (
    DEFAULT_MODEL,
    DELETION_CANDIDATE_ROLES,
    EFFORT_LEVELS,
    MAX_OUTPUT_TOKENS,
    AnalysisError,
    BatchResult,
    Progress,
    analysis_input,
    relative_entries,
    stream_and_log,
)
from dupebot.calllog import CallLog

# The most suggestions: 25, or a quarter of the clusters analysed, whichever is
# smaller (rounded up, so at least one when there are any clusters).
SUGGESTION_CEILING = 25
SUGGESTION_SHARE = 0.25
# Model requests per run. The last one may not call tools, so the agent always
# ends with an answer.
MAX_TURNS = 10
MAX_FOLDER_ENTRIES = 200

# Tool inputs are a path or two, so the API's buffered, schema-checked input
# (strict) is worth more here than streaming the input as it is generated.
TOOLS = [
    {
        "name": "compare_files",
        "description": "Check whether two files from the clusters have exactly the same "
        "contents, byte for byte. Call it before suggesting the deletion of a file you "
        "believe is a copy. Returns identical true or false, and both sizes. Read-only.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "path_a": {"type": "string", "description": "A file path exactly as given."},
                "path_b": {"type": "string", "description": "Another file path exactly as given."},
            },
            "required": ["path_a", "path_b"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_folder",
        "description": "List the files and subfolders in a folder inside the scanned folder, "
        "with sizes and modified dates. Call it when where a file sits matters to a "
        "suggestion, e.g. an archive folder, a drafts folder, or nearby versions. "
        f"Returns at most {MAX_FOLDER_ENTRIES} entries. Read-only.",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "folder": {
                    "type": "string",
                    "description": "Folder path relative to the scanned folder, as in the "
                    'file paths; "" for the scanned folder itself.',
                }
            },
            "required": ["folder"],
            "additionalProperties": False,
        },
    },
]

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "suggestions": {
            "type": "array",
            "description": "Safest and most worthwhile first. Can be empty.",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "string"},
                    "delete": {"type": "string", "description": "Path exactly as given."},
                    "keep": {
                        "type": "string",
                        "description": "Path exactly as given: the file that supersedes it.",
                    },
                    "reason": {"type": "string", "description": "One or two sentences."},
                },
                "required": ["cluster_id", "delete", "keep", "reason"],
                "additionalProperties": False,
            },
        },
        "summary": {
            "type": "string",
            "description": "One or two sentences for the person about what you found.",
        },
    },
    "required": ["suggestions", "summary"],
    "additionalProperties": False,
}


@dataclass(frozen=True)
class Check:
    """One tool call the agent made, for showing the person what was checked."""

    tool: str
    target: str
    outcome: str
    is_error: bool = False


@dataclass(frozen=True)
class Suggestion:
    cluster_id: str
    delete: str  # paths relative to the scanned folder, as the model saw them
    keep: str
    reason: str
    identical: bool | None  # from our own compare_files results; None if never compared


@dataclass
class SuggestResult:
    suggestions: list[Suggestion]
    summary: str
    cap: int
    candidates: int
    checks: list[Check] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    thinking_tokens: int | None = None


class SuggestionError(AnalysisError):
    """The agent did not produce a usable answer."""


def suggestion_cap(clusters: int) -> int:
    """The most suggestions the agent may make for this many analysed clusters."""
    if clusters <= 0:
        return 0
    return min(SUGGESTION_CEILING, math.ceil(clusters * SUGGESTION_SHARE))


def count_candidates(results: list[BatchResult]) -> int:
    return sum(
        1
        for r in results
        for c in r.clusters
        for g in c["groups"]
        for f in g["files"]
        if f["role"] in DELETION_CANDIDATE_ROLES
    )


def system_prompt() -> str:
    return files("dupebot").joinpath("suggest_prompt.md").read_text(encoding="utf-8")


class FileTools:
    """The agent's read-only tools, limited to the scanned folder.

    compare_files accepts only files from the report's clusters; list_folder only
    folders inside the scanned folder. Paths are always resolved here, so the
    model cannot reach outside the scan with "..", absolute paths or links.
    """

    def __init__(self, report: dict):
        self.root = Path(report["root"]).resolve()
        self.files: dict[str, Path] = {
            rel: Path(entry["path"])
            for by_path in relative_entries(report).values()
            for rel, entry in by_path.items()
        }
        self.checks: list[Check] = []
        self.identical: set[frozenset[str]] = set()
        self.compared: set[frozenset[str]] = set()
        self._hashes: dict[tuple[str, int, float], str] = {}

    def run(self, name: str, tool_input: object) -> tuple[str, bool]:
        """Run one tool call. Returns (result text for the model, is_error)."""
        try:
            if not isinstance(tool_input, dict):
                raise ToolInputError("input must be an object")
            if name == "compare_files":
                result = self._compare(_text(tool_input, "path_a"), _text(tool_input, "path_b"))
            elif name == "list_folder":
                result = self._list_folder(_text(tool_input, "folder"))
            else:
                raise ToolInputError(f"no tool named {name!r}")
        except ToolInputError as e:
            self.checks.append(Check(name, _describe(tool_input), str(e), is_error=True))
            return json.dumps({"error": str(e)}), True
        return json.dumps(result, ensure_ascii=False), False

    def _compare(self, a: str, b: str) -> dict:
        path_a, path_b = self._clustered(a), self._clustered(b)
        if a == b:
            raise ToolInputError("those are the same file")
        stat_a, stat_b = path_a.stat(), path_b.stat()
        if stat_a.st_size != stat_b.st_size:
            identical, method = False, "sizes differ"
        else:
            identical = self._sha256(path_a, stat_a) == self._sha256(path_b, stat_b)
            method = "compared contents"
        pair = frozenset((a, b))
        self.compared.add(pair)
        if identical:
            self.identical.add(pair)
        self.checks.append(
            Check(
                "compare_files", f"{a} ↔ {b}", "identical" if identical else f"different ({method})"
            )
        )
        return {"identical": identical, "size_a": stat_a.st_size, "size_b": stat_b.st_size}

    def _list_folder(self, folder: str) -> dict:
        target = (self.root / folder).resolve()
        if target != self.root and self.root not in target.parents:
            raise ToolInputError("that folder is outside the scanned folder")
        if target.is_symlink() or not target.is_dir():
            raise ToolInputError("not a folder inside the scanned folder")
        entries = []
        with os.scandir(target) as it:
            for entry in sorted(it, key=lambda e: e.name.lower()):
                if entry.is_symlink():
                    kind, size, modified = "link", None, None
                else:
                    info = entry.stat()
                    kind = "folder" if entry.is_dir() else "file"
                    size = info.st_size if kind == "file" else None
                    modified = datetime.fromtimestamp(info.st_mtime).isoformat(timespec="seconds")
                entries.append(
                    {"name": entry.name, "kind": kind, "size_bytes": size, "modified": modified}
                )
        shown = entries[:MAX_FOLDER_ENTRIES]
        rel = str(target.relative_to(self.root)) if target != self.root else ""
        self.checks.append(
            Check("list_folder", rel or "(scanned folder)", f"{len(entries)} entries")
        )
        return {
            "folder": rel,
            "entries": shown,
            "total_entries": len(entries),
            "truncated": len(entries) > len(shown),
        }

    def _clustered(self, rel: str) -> Path:
        path = self.files.get(rel)
        if path is None:
            raise ToolInputError(f"not a file from the clusters: {rel!r}")
        if not path.is_file() or path.is_symlink():
            raise ToolInputError(f"no longer there: {rel!r}")
        return path

    def _sha256(self, path: Path, info: os.stat_result) -> str:
        key = (str(path), info.st_size, info.st_mtime)
        if key not in self._hashes:
            digest = hashlib.sha256()
            with path.open("rb") as f:  # read-only
                for block in iter(lambda: f.read(1 << 20), b""):
                    digest.update(block)
            self._hashes[key] = digest.hexdigest()
        return self._hashes[key]


class ToolInputError(Exception):
    """A tool call the agent should be told it got wrong."""


def suggest(
    report: dict,
    results: list[BatchResult],
    model: str = DEFAULT_MODEL,
    client: anthropic.Anthropic | None = None,
    log: CallLog | None = None,
    effort: str | None = None,
    run_id: str | None = None,
    progress: Progress | None = None,
) -> SuggestResult:
    """Run the suggestions agent over an analysed report. progress, if given, is
    told what the agent is doing (which turn, which check)."""
    if effort is not None and effort not in EFFORT_LEVELS:
        raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}, got {effort!r}")
    candidates = count_candidates(results)
    cap = suggestion_cap(sum(len(r.clusters) for r in results))
    if candidates == 0:  # nothing flagged to consider deleting, so nothing to prioritize
        return SuggestResult([], "The analysis found no earlier versions or copies.", cap, 0)

    client = client or anthropic.Anthropic()
    tools = FileTools(report)
    prompt = system_prompt()
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    run_id = run_id or uuid.uuid4().hex
    output_config: dict = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    if effort:
        output_config["effort"] = effort
    payload = json.dumps(_agent_input(report, results, cap), ensure_ascii=False)
    messages: list[dict] = [{"role": "user", "content": payload}]
    out = SuggestResult([], "", cap, candidates)

    for turn in range(1, MAX_TURNS + 1):
        last_turn = turn == MAX_TURNS
        if progress:
            checks = len(tools.checks)
            progress(
                f"turn {turn} of up to {MAX_TURNS}, "
                + ("deciding what to check…" if turn == 1 else f"{checks} check(s) made so far…")
                + (" Writing its answer." if last_turn else "")
            )
        call = {
            "run_id": run_id,
            "called_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "purpose": "suggest",
            "model": model,
            "effort": effort,
            "prompt_sha": prompt_sha,
            "input_chars": len(prompt) + sum(len(json.dumps(m, default=str)) for m in messages),
        }
        message, text, thinking = stream_and_log(
            client,
            log,
            call,
            model=model,
            max_tokens=MAX_OUTPUT_TOKENS,
            system=prompt,
            messages=messages,
            tools=TOOLS,
            # On the last turn tools are off, so the agent has to answer.
            tool_choice={"type": "none"} if last_turn else {"type": "auto"},
            output_config=output_config,
        )
        out.calls += 1
        out.input_tokens += message.usage.input_tokens
        out.output_tokens += message.usage.output_tokens
        if thinking is not None:
            out.thinking_tokens = (out.thinking_tokens or 0) + thinking

        if message.stop_reason == "refusal":
            raise SuggestionError("the model declined to make suggestions")
        if message.stop_reason == "max_tokens":
            raise SuggestionError(f"the answer was cut off at {MAX_OUTPUT_TOKENS} tokens")
        tool_calls = [b for b in message.content if b.type == "tool_use"]
        if message.stop_reason == "tool_use" and tool_calls and not last_turn:
            messages.append({"role": "assistant", "content": message.content})
            results_block = []
            for block in tool_calls:
                if progress:
                    progress(f"turn {turn} of up to {MAX_TURNS}, {_describe_call(block)}")
                result, is_error = tools.run(block.name, block.input)
                entry = {"type": "tool_result", "tool_use_id": block.id, "content": result}
                if is_error:
                    entry["is_error"] = True
                results_block.append(entry)
            messages.append({"role": "user", "content": results_block})  # all in one message
            continue
        try:
            answer = json.loads(text)
            raw, summary = answer["suggestions"], answer["summary"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            raise SuggestionError(f"the answer was not in the expected format ({e})") from None
        out.suggestions, out.warnings = check_suggestions(raw, report, cap, tools)
        out.summary = summary
        out.checks = tools.checks
        return out
    raise SuggestionError(
        f"no answer after {MAX_TURNS} turns"
    )  # not reached: last turn has no tools


def check_suggestions(
    raw: list[dict], report: dict, cap: int, tools: FileTools
) -> tuple[list[Suggestion], list[str]]:
    """Keep only suggestions that hold up against the scan. Returns them and
    a warning for each one dropped."""
    by_cluster = relative_entries(report)
    kept: list[Suggestion] = []
    warnings: list[str] = []
    deleting: set[str] = set()
    keeping: set[str] = set()
    for s in raw:
        cid, delete, keep = s["cluster_id"], s["delete"], s["keep"]
        cluster = by_cluster.get(cid, {})
        problem = None
        if delete not in cluster or keep not in cluster:
            problem = "names a file that is not in that cluster"
        elif delete == keep:
            problem = "would keep and delete the same file"
        elif delete in deleting:
            problem = "repeats a file already suggested"
        elif delete in keeping or keep in deleting:
            problem = "conflicts with another suggestion (could delete both copies)"
        elif len(kept) >= cap:
            problem = f"is over the limit of {cap}"
        if problem:
            warnings.append(f"Dropped a suggestion to delete {delete}: it {problem}.")
            continue
        pair = frozenset((delete, keep))
        identical = True if pair in tools.identical else (False if pair in tools.compared else None)
        kept.append(Suggestion(cid, delete, keep, s["reason"], identical))
        deleting.add(delete)
        keeping.add(keep)
    return kept, warnings


def _agent_input(report: dict, results: list[BatchResult], cap: int) -> dict:
    analysed = {c["cluster_id"]: c["groups"] for r in results for c in r.clusters}
    clusters = [
        {**c, "analysis": analysed[c["cluster_id"]]}
        for c in analysis_input(report)["clusters"]
        if c["cluster_id"] in analysed
    ]
    return {"max_suggestions": cap, "clusters": clusters}


def _describe_call(block) -> str:
    args = block.input if isinstance(block.input, dict) else {}
    if block.name == "compare_files":
        return f"comparing {args.get('path_a', '?')} with {args.get('path_b', '?')}"
    if block.name == "list_folder":
        return f"listing {args.get('folder') or 'the scanned folder'}"
    return f"running {block.name}"


def _text(tool_input: dict, key: str) -> str:
    value = tool_input.get(key)
    if not isinstance(value, str):
        raise ToolInputError(f"{key} must be a string")
    return value


def _describe(tool_input: object) -> str:
    if isinstance(tool_input, dict):
        return ", ".join(str(v) for v in tool_input.values())
    return str(tool_input)
