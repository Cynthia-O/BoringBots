"""A local, append-only log of every call DupeBot makes to an LLM.

Kept for FinOps (what each run cost) and LLMOps (latency, failures, and how
results change across models and prompt versions). Rows are only ever added,
never updated or deleted.

The database lives at ~/.dupebot/dupebot.sqlite, outside the repo so that
nothing in it can be committed by accident. Set DUPEBOT_DB to use another file.
"""

import os
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_calls (
    id              INTEGER PRIMARY KEY,
    run_id          TEXT NOT NULL,     -- groups the calls made by one DupeBot run
    called_at       TEXT NOT NULL,     -- when the request was sent, UTC, ISO 8601
    purpose         TEXT NOT NULL,     -- which step made the call, e.g. "analyze"
    model           TEXT NOT NULL,     -- model requested
    effort          TEXT,              -- thinking effort requested; NULL means the model's default
    response_model  TEXT,              -- model that answered, as reported by the API
    prompt_sha      TEXT NOT NULL,     -- first 12 hex digits of the system prompt's SHA-256
    input_chars     INTEGER NOT NULL,  -- system prompt plus message, in characters
    output_chars    INTEGER,           -- text received, in characters
    input_tokens    INTEGER,           -- as billed
    output_tokens   INTEGER,           -- as billed, thinking included
    thinking_tokens INTEGER,           -- the part of output_tokens spent thinking, as reported
    duration_ms     INTEGER NOT NULL,
    stop_reason     TEXT,              -- end_turn, max_tokens, refusal, ...
    error           TEXT,              -- set when the call failed
    request_id      TEXT               -- the API's request id, for support tickets
);
"""


@dataclass(frozen=True)
class CallRecord:
    run_id: str
    called_at: str
    purpose: str
    model: str
    prompt_sha: str
    input_chars: int
    duration_ms: int
    effort: str | None = None
    response_model: str | None = None
    output_chars: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    thinking_tokens: int | None = None
    stop_reason: str | None = None
    error: str | None = None
    request_id: str | None = None


_TEXT_COLUMNS = frozenset(
    {
        "run_id",
        "called_at",
        "purpose",
        "model",
        "effort",
        "response_model",
        "prompt_sha",
        "stop_reason",
        "error",
        "request_id",
    }
)


def default_db_path() -> Path:
    configured = os.environ.get("DUPEBOT_DB")
    return (
        Path(configured).expanduser() if configured else Path.home() / ".dupebot" / "dupebot.sqlite"
    )


class CallLog:
    def __init__(self, path: Path | None = None):
        self.path = path or default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.executescript(SCHEMA)
        self._add_missing_columns()

    def _add_missing_columns(self) -> None:
        """Bring a log made by an older DupeBot up to the current schema. Columns
        are only ever added, so existing rows are kept as they are (new columns
        are empty for them)."""
        have = {row[1] for row in self._conn.execute("PRAGMA table_info(llm_calls)")}
        with self._conn:
            for name in CallRecord.__dataclass_fields__:
                if name not in have:
                    kind = "TEXT" if name in _TEXT_COLUMNS else "INTEGER"
                    self._conn.execute(f"ALTER TABLE llm_calls ADD COLUMN {name} {kind}")

    def record(self, call: CallRecord) -> None:
        row = asdict(call)
        columns = ", ".join(row)
        placeholders = ", ".join(f":{name}" for name in row)
        with self._conn:  # commits, so a crash later in the run keeps this row
            self._conn.execute(f"INSERT INTO llm_calls ({columns}) VALUES ({placeholders})", row)

    def rows(self, run_id: str | None = None) -> list[dict]:
        """Every logged call, or only those of one run."""
        if run_id is None:
            cursor = self._conn.execute("SELECT * FROM llm_calls ORDER BY id")
        else:
            cursor = self._conn.execute(
                "SELECT * FROM llm_calls WHERE run_id = ? ORDER BY id", (run_id,)
            )
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, values, strict=True)) for values in cursor]

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "CallLog":
        return self

    def __exit__(self, *exc) -> None:
        self.close()
