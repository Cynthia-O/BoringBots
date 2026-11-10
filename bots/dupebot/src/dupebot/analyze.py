"""Stage 2: the agent analyses the stage 1 clusters.

The system prompt lives in analyze_prompt.md. It holds no per-run values, so it
stays identical between runs and can be cached.

Credentials come from the environment, normally loaded from a git-ignored .env
file (see .env.example at the repo root and dupebot.env).
"""

import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.resources import files
from pathlib import Path

import anthropic

from dupebot.calllog import CallLog, CallRecord

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


# Models offered in the interactive menu, cheapest first. --model accepts others.
MODEL_CHOICES = (
    ModelChoice("claude-haiku-5-5", "cheapest, fastest", "medium"),
    ModelChoice("claude-sonnet-5-5", "about 20x Haiku's price", "high"),
    ModelChoice("claude-opus-5-5", "about 40x Haiku's price, most capable", "medium"),
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


class AnalysisError(Exception):
    """The model call did not produce a usable answer."""


@dataclass(frozen=True)
class BatchResult:
    cluster_ids: tuple[str, ...]
    text: str
    input_tokens: int
    output_tokens: int


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
) -> list[BatchResult]:
    """Send the report's clusters to the model, one request per batch.

    effort sets how much the model thinks (see EFFORT_LEVELS); None leaves the
    model's default. Every request, including failed ones, is recorded in log
    when one is given.
    """
    if effort is not None and effort not in EFFORT_LEVELS:
        raise ValueError(f"effort must be one of {', '.join(EFFORT_LEVELS)}, got {effort!r}")
    # Only sent when set, so the model's own default applies otherwise.
    extra = {"output_config": {"effort": effort}} if effort else {}
    client = client or anthropic.Anthropic()
    prompt = system_prompt()
    prompt_sha = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:12]
    run_id = uuid.uuid4().hex
    results = []
    for batch in batch_clusters(analysis_input(report)["clusters"]):
        payload = json.dumps({"clusters": batch}, ensure_ascii=False)
        call = {
            "run_id": run_id,
            "called_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "purpose": "analyze",
            "model": model,
            "effort": effort,
            "prompt_sha": prompt_sha,
            "input_chars": len(prompt) + len(payload),
        }
        started = time.monotonic()
        try:
            # Streaming keeps long answers from hitting HTTP timeouts.
            with client.messages.stream(
                model=model,
                max_tokens=MAX_OUTPUT_TOKENS,
                system=prompt,
                messages=[{"role": "user", "content": payload}],
                **extra,
            ) as stream:
                message = stream.get_final_message()
        except Exception as e:
            if log:
                log.record(
                    CallRecord(
                        **call, duration_ms=_ms_since(started), error=f"{type(e).__name__}: {e}"
                    )
                )
            raise
        text = "".join(b.text for b in message.content if b.type == "text")
        if log:
            log.record(
                CallRecord(
                    **call,
                    duration_ms=_ms_since(started),
                    response_model=getattr(message, "model", None),
                    output_chars=len(text),
                    input_tokens=message.usage.input_tokens,
                    output_tokens=message.usage.output_tokens,
                    stop_reason=message.stop_reason,
                    request_id=getattr(message, "_request_id", None),
                )
            )
        if message.stop_reason == "refusal":
            raise AnalysisError("the model declined to analyse this batch")
        if message.stop_reason == "max_tokens":
            raise AnalysisError(
                f"the answer was cut off at {MAX_OUTPUT_TOKENS} tokens; try fewer files per request"
            )
        results.append(
            BatchResult(
                cluster_ids=tuple(c["cluster_id"] for c in batch),
                text=text,
                input_tokens=message.usage.input_tokens,
                output_tokens=message.usage.output_tokens,
            )
        )
    return results


def _ms_since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)
