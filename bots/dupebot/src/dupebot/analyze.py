"""Stage 2: the agent analyses the stage 1 clusters.

The system prompt lives in analyze_prompt.md. It holds no per-run values, so it
stays identical between runs and can be cached.

The answer's format is not described in the prompt. It is enforced by the API
through OUTPUT_SCHEMA (structured outputs), so every answer parses.

Credentials come from the environment, normally loaded from a git-ignored .env
file (see .env.example at the repo root and dupebot.env).
"""

import hashlib
import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import anthropic

from bottrace import span
from dupebot.calllog import CallLog, CallRecord
from finops.pricing import call_cost

DEFAULT_MODEL = "claude-haiku-5-5"
# Whole clusters are packed into one request up to this many files. A cluster
# is never split, so a request can exceed this when one cluster alone does.
MAX_FILES_PER_REQUEST = 200
# Covers thinking and the visible answer together. Only tokens actually used are
# billed, so a roomy cap costs nothing unless an answer needs it.
MAX_OUTPUT_TOKENS = 64_000
# Thinking effort levels the API accepts. More effort means more thinking, which
# is billed as output tokens but not returned as text.
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class ModelChoice:
    model: str
    note: str
    default_effort: str  # the effort the model uses when none is requested
    label: str = ""  # short display name


# Models offered in the interactive menu, cheapest first. --model accepts others.
MODEL_CHOICES = (
    ModelChoice("claude-haiku-5-5", "cheapest, fastest", "medium", "Haiku 5.5"),
    ModelChoice("claude-sonnet-5-5", "about 20x Haiku's price", "high", "Sonnet 5.5"),
    ModelChoice("claude-opus-5-5", "about 40x Haiku's price, most capable", "medium", "Opus 5.5"),
)

# Report keys kept out of the model's input. They describe how the clustering ran,
# which matters to people reviewing a run, not to the analysis. The scanned
# folder ("root") is left out too: paths are sent relative to it, which saves
# tokens and keeps the person's home folder name out of the request.
OMITTED_FROM_INPUT = frozenset({"root", "settings", "summary", "empty_after_cleaning"})
# Per-file keys kept out of the model's input, though all are stored locally.
# The cleaning-derived fields are left out because the model judges files by
# their original names, and our labels (sometimes wrong) would only steer it.
# "name" is left out because it is already the last part of "path".
OMITTED_FILE_FIELDS = frozenset({"name", "cleaned_name", "dates", "version_markers", "set_markers"})


# How the files in a group relate. One relationship per group.
RELATIONSHIPS = ("copies", "version_series", "variants", "recurring", "parts", "unrelated")
# Each file's place within its group.
ROLES = ("current_version", "earlier_version", "original", "copy", "member")
# Roles that make a file a candidate for the person to consider deleting.
DELETION_CANDIDATE_ROLES = frozenset({"earlier_version", "copy"})

# The shape of every answer, enforced by the API. Descriptions here are read by
# the model, so field meanings live with the fields rather than in the prompt.
OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "description": "One entry per cluster in the input.",
            "items": {
                "type": "object",
                "properties": {
                    "cluster_id": {"type": "string"},
                    "groups": {
                        "type": "array",
                        "description": "The cluster's files sorted into groups of related "
                        "files. Every file in the cluster appears in exactly one group.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "files": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "path": {
                                                "type": "string",
                                                "description": "Exactly as in the input.",
                                            },
                                            "role": {
                                                "type": "string",
                                                "enum": list(ROLES),
                                                "description": "current_version and "
                                                "earlier_version for version series; original "
                                                "and copy for copies; member for everything "
                                                "else, where no file is preferred.",
                                            },
                                        },
                                        "required": ["path", "role"],
                                        "additionalProperties": False,
                                    },
                                },
                                "reason": {
                                    "type": "string",
                                    "description": "The evidence for this grouping, citing "
                                    "names, sizes, dates and authors. One to three sentences.",
                                },
                                "relationship": {"type": "string", "enum": list(RELATIONSHIPS)},
                                "check_before_deleting": {
                                    "type": "string",
                                    "description": "What the person should check before "
                                    "deleting anything in this group, or an empty string.",
                                },
                            },
                            "required": [
                                "files",
                                "reason",
                                "relationship",
                                "check_before_deleting",
                            ],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["cluster_id", "groups"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}


# Receives a short line of text describing how far a run has got.
Progress = Callable[[str], None]


class AnalysisError(Exception):
    """The model call did not produce a usable answer."""


@dataclass(frozen=True)
class BatchResult:
    cluster_ids: tuple[str, ...]
    clusters: list[dict]  # the parsed answer: OUTPUT_SCHEMA's "clusters"
    warnings: list[str]  # where the answer and the input disagree
    input_tokens: int
    output_tokens: int  # as billed: thinking plus the visible answer
    thinking_tokens: int | None = None  # the part of output_tokens spent thinking, if reported


def system_prompt() -> str:
    return files("dupebot").joinpath("analyze_prompt.md").read_text(encoding="utf-8")


def analysis_input(report: dict) -> dict:
    """The part of a stage 1 report that is sent to the model. Does not modify report."""
    root = Path(report["root"])
    data = {key: value for key, value in report.items() if key not in OMITTED_FROM_INPUT}
    data["clusters"] = [
        {**cluster, "files": [_file_input(f, root) for f in cluster["files"]]}
        for cluster in report["clusters"]
    ]
    return data


def _file_input(entry: dict, root: Path) -> dict:
    data = {k: v for k, v in entry.items() if k not in OMITTED_FILE_FIELDS}
    try:
        data["path"] = str(Path(entry["path"]).relative_to(root))
    except ValueError:
        pass  # not under root; keep the path as it is
    return data


def batch_clusters(
    clusters: list[dict], max_files: int = MAX_FILES_PER_REQUEST
) -> list[list[dict]]:
    """Pack whole clusters into batches of at most max_files files each."""
    batches: list[list[dict]] = []
    current: list[dict] = []
    count = 0
    for cluster in clusters:
        size = len(cluster["files"])
        if current and count + size > max_files:
            batches.append(current)
            current, count = [], 0
        current.append(cluster)
        count += size
    if current:
        batches.append(current)
    return batches


def analyze(
    report: dict,
    model: str = DEFAULT_MODEL,
    client: anthropic.Anthropic | None = None,
    log: CallLog | None = None,
    effort: str | None = None,
    run_id: str | None = None,
    progress: Progress | None = None,
    on_cluster: Callable[[dict], None] | None = None,
) -> list[BatchResult]:
    """Send the report's clusters to the model, one request per batch.

    effort sets how much the model thinks (see EFFORT_LEVELS); None leaves the
    model's default. Every request, including failed ones, is recorded in log
    when one is given, under run_id (a new one when None). While answers stream
    in, on_cluster (if given) receives each cluster's answer as soon as it is
    complete, and progress (if given) a short line of text on how far it has got.
    """
    if effort is not None and effort not in EFFORT_LEVELS:
        raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}, got {effort!r}")
    output_config: dict = {"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}}
    if effort:  # only sent when set, so the model's own default applies otherwise
        output_config["effort"] = effort
    client = client or anthropic.Anthropic()
    prompt = system_prompt()
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    run_id = run_id or uuid.uuid4().hex
    results = []
    batches = batch_clusters(analysis_input(report)["clusters"])
    total = sum(len(b) for b in batches)
    with span("analyze", kind="stage", clusters=total, requests=len(batches)):
        done = 0
        for number, batch in enumerate(batches, 1):
            payload = json.dumps({"clusters": batch}, ensure_ascii=False)
            where = f" (request {number} of {len(batches)})" if len(batches) > 1 else ""
            answered = done

            def arrived(entry: dict, where: str = where) -> None:
                nonlocal answered
                answered += 1
                if on_cluster:
                    on_cluster(entry)
                if progress:
                    progress(f"Analysing: {answered} of {total} clusters answered{where}")

            stream = ClusterStream(arrived) if (progress or on_cluster) else None
            if progress:
                progress(
                    f"Sent {done + len(batch)} of {total} clusters{where}. The model is thinking…"
                )
            call = {
                "run_id": run_id,
                "called_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "purpose": "analyze",
                "model": model,
                "effort": effort,
                "prompt_sha": prompt_sha,
                "input_chars": len(prompt) + len(payload),
            }
            message, text, thinking_tokens = stream_and_log(
                client,
                log,
                call,
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=prompt,
                messages=[{"role": "user", "content": payload}],
                output_config=output_config,
                on_text=stream.feed if stream else None,
                span_attrs={"request": number, "clusters": len(batch)},
            )
            if message.stop_reason == "refusal":
                raise AnalysisError("the model declined to analyse this batch")
            if message.stop_reason == "max_tokens":
                raise AnalysisError(
                    f"the answer was cut off at {MAX_OUTPUT_TOKENS} tokens; "
                    "try fewer files per request"
                )
            try:
                answer = json.loads(text)["clusters"]
            except (json.JSONDecodeError, KeyError, TypeError) as e:
                raise AnalysisError(f"the answer was not in the expected format ({e})") from None
            results.append(
                BatchResult(
                    cluster_ids=tuple(c["cluster_id"] for c in batch),
                    clusters=answer,
                    warnings=check_answer(batch, answer),
                    input_tokens=message.usage.input_tokens,
                    output_tokens=message.usage.output_tokens,
                    thinking_tokens=thinking_tokens,
                )
            )
            done += len(batch)
            if progress:
                progress(f"{done} of {total} clusters analysed and logged.")
        return results


def stream_and_log(
    client,
    log: CallLog | None,
    call: dict,
    on_text: Callable[[str], None] | None = None,
    span_attrs: dict | None = None,
    **request,
):
    """Make one streamed model call, log it and trace it, failed or not.

    call holds the log fields known before the call (run_id, purpose, model, ...).
    on_text, if given, receives the answer's visible text piece by piece as it
    streams in (thinking is not included).
    Returns the final message, its visible text, and its thinking token count
    (None when the API does not report it). Shared by every DupeBot model call so
    each one is logged, counted and traced the same way. span_attrs are extra
    attributes for the call's trace span (e.g. which batch or turn).
    """
    with span(f"{call['purpose']} call", kind="llm", **(span_attrs or {})) as s:
        s.set(
            purpose=call["purpose"],
            model=call["model"],
            effort=call.get("effort") or "model default",
            prompt_sha=call["prompt_sha"],
            input_chars=call["input_chars"],
        )
        message, text, thinking_tokens = _stream(client, log, call, on_text, s, request)
        usage = message.usage
        cost = call_cost(call["model"], usage.input_tokens, usage.output_tokens, thinking_tokens)
        s.set(
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            thinking_tokens=thinking_tokens,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", None),
            cache_write_tokens=getattr(usage, "cache_creation_input_tokens", None),
            cost_usd=cost.total if cost else None,
            output_chars=len(text),
            stop_reason=message.stop_reason,
            response_model=getattr(message, "model", None),
            request_id=getattr(message, "_request_id", None),
        )
    return message, text, thinking_tokens


def _stream(client, log, call, on_text, s, request):
    started = time.monotonic()
    try:
        # Streaming keeps long answers from hitting HTTP timeouts. Reading the
        # events one by one also times the first byte, thinking and text.
        with client.messages.stream(**request) as stream:
            for event in stream:
                s.event("first_event", once=True)
                kind = getattr(event, "type", None)
                if kind == "content_block_start" and getattr(
                    getattr(event, "content_block", None), "type", None
                ) in ("thinking", "redacted_thinking"):
                    s.event("first_thinking", once=True)
                elif kind == "text":
                    s.event("first_text", once=True)
                    if on_text is not None:
                        on_text(event.text)
            message = stream.get_final_message()
    except Exception as e:
        if log:
            log.record(
                CallRecord(**call, duration_ms=_ms_since(started), error=f"{type(e).__name__}: {e}")
            )
        raise
    text = "".join(b.text for b in message.content if b.type == "text")
    details = getattr(message.usage, "output_tokens_details", None)
    thinking_tokens = getattr(details, "thinking_tokens", None)
    if log:
        log.record(
            CallRecord(
                **call,
                duration_ms=_ms_since(started),
                response_model=getattr(message, "model", None),
                output_chars=len(text),
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
                thinking_tokens=thinking_tokens,
                stop_reason=message.stop_reason,
                request_id=getattr(message, "_request_id", None),
            )
        )
    return message, text, thinking_tokens


class ClusterStream:
    """Picks complete cluster entries out of an answer while it streams in.

    The answer is {"clusters": [{...}, {...}, ...]}. Each time an entry's closing
    brace arrives, the entry is decoded and passed to on_cluster, so a page can
    show it before the rest of the answer exists. The final, complete answer is
    still parsed and checked as usual; this is only for showing progress.
    """

    def __init__(self, on_cluster: Callable[[dict], None]):
        self.on_cluster = on_cluster
        self.text = ""
        self.pos: int | None = None  # where the next entry may start, once "[" is seen
        self.decoder = json.JSONDecoder()

    def feed(self, piece: str) -> None:
        self.text += piece
        if self.pos is None:
            key = self.text.find('"clusters"')
            start = self.text.find("[", key) if key >= 0 else -1
            if start < 0:
                return
            self.pos = start + 1
        while True:
            i = self.pos
            while i < len(self.text) and self.text[i] in " \t\r\n,":
                i += 1
            if i >= len(self.text) or self.text[i] != "{":
                return  # nothing more yet, or the end of the list
            try:
                entry, end = self.decoder.raw_decode(self.text, i)
            except json.JSONDecodeError:
                return  # this entry is still arriving
            self.pos = end
            if isinstance(entry, dict) and "cluster_id" in entry:
                self.on_cluster(entry)


def relative_entries(report: dict) -> dict[str, dict[str, dict]]:
    """cluster_id -> {path as the model sees it -> full report entry}."""
    sent = analysis_input(report)["clusters"]
    return {
        full["cluster_id"]: {
            s["path"]: f for s, f in zip(trimmed["files"], full["files"], strict=True)
        }
        for full, trimmed in zip(report["clusters"], sent, strict=True)
    }


def check_answer(batch: list[dict], answer: list[dict]) -> list[str]:
    """Ways the answer fails to account for the batch exactly: clusters or files
    left out, listed twice, or not in the input. The schema fixes the shape of an
    answer but cannot check it against the input, so this does.
    """
    warnings = []
    expected = {c["cluster_id"]: [f["path"] for f in c["files"]] for c in batch}
    seen_clusters = set()
    for cluster in answer:
        cid = cluster["cluster_id"]
        if cid not in expected:
            warnings.append(f"{cid}: not a cluster in the input")
            continue
        if cid in seen_clusters:
            warnings.append(f"{cid}: answered more than once")
            continue
        seen_clusters.add(cid)
        paths = [f["path"] for g in cluster["groups"] for f in g["files"]]
        for path in sorted(set(expected[cid]) - set(paths)):
            warnings.append(f"{cid}: {path} was left out")
        for path in sorted(set(paths) - set(expected[cid])):
            warnings.append(f"{cid}: {path} is not in this cluster")
        for path in sorted({p for p in paths if paths.count(p) > 1}):
            warnings.append(f"{cid}: {path} is in more than one group")
    for cid in expected:
        if cid not in seen_clusters:
            warnings.append(f"{cid}: not analysed")
    return warnings


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
