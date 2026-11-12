"""Read every bot's LLM call log and total the spend by day.

Each bot that calls a model keeps an append-only SQLite log with an `llm_calls`
table (see dupebot/calllog.py). This module only reads those logs, opening them
read-only, so it can never change one. To add a bot, add it to SOURCES.
"""

import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from finops.pricing import Cost, call_cost


@dataclass(frozen=True)
class Source:
    bot: str
    env_var: str  # overrides the default location, as the bot itself does
    default: Path

    def path(self) -> Path:
        configured = os.environ.get(self.env_var)
        return Path(configured).expanduser() if configured else self.default


# Every bot's call log. Keep each entry in step with how that bot finds its log
# (for DupeBot: dupebot.calllog.default_db_path).
SOURCES = (Source("DupeBot", "DUPEBOT_DB", Path.home() / ".dupebot" / "dupebot.sqlite"),)


@dataclass
class Totals:
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0  # billed: thinking plus the visible answer
    thinking_tokens: int = 0
    cost: Cost = field(default_factory=Cost)
    unpriced: int = 0  # calls to a model with no known price, left out of cost
    thinking_unreported: int = 0  # calls with output but no separate thinking count

    @property
    def response_tokens(self) -> int:
        return self.output_tokens - self.thinking_tokens

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def add(self, row: dict) -> None:
        input_tokens = row.get("input_tokens") or 0
        output_tokens = row.get("output_tokens") or 0
        thinking = row.get("thinking_tokens")
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.thinking_tokens += thinking or 0
        if thinking is None and output_tokens:
            self.thinking_unreported += 1
        cost = call_cost(row["model"], input_tokens, output_tokens, thinking)
        if cost is None:
            self.unpriced += 1
        else:
            self.cost += cost


@dataclass
class Ledger:
    days: dict[date, Totals]  # every day from the first call to the last, empty days included
    by_bot_model: dict[tuple[str, str], Totals]
    total: Totals
    found: list[tuple[Source, Path, int]]  # sources read, with how many calls each
    missing: list[tuple[Source, Path]]  # sources with no log yet


def read_calls(path: Path) -> list[dict]:
    """Every row of a call log, opened read-only."""
    uri = f"{path.resolve().as_uri()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cursor = conn.execute("SELECT * FROM llm_calls ORDER BY called_at")
        names = [d[0] for d in cursor.description]
        return [dict(zip(names, values, strict=True)) for values in cursor]
    finally:
        conn.close()


def build_ledger(sources=SOURCES) -> Ledger:
    days: dict[date, Totals] = defaultdict(Totals)
    by_bot_model: dict[tuple[str, str], Totals] = defaultdict(Totals)
    total = Totals()
    found, missing = [], []
    for source in sources:
        path = source.path()
        if not path.is_file():
            missing.append((source, path))
            continue
        rows = read_calls(path)
        found.append((source, path, len(rows)))
        for row in rows:
            day = _local_day(row["called_at"])
            days[day].add(row)
            by_bot_model[(source.bot, row["model"])].add(row)
            total.add(row)
    if days:  # fill the gaps so charts show quiet days as zero
        first, last = min(days), max(days)
        filled = {
            first + timedelta(n): days.get(first + timedelta(n), Totals())
            for n in range((last - first).days + 1)
        }
    else:
        filled = {}
    return Ledger(filled, dict(by_bot_model), total, found, missing)


def _local_day(called_at: str) -> date:
    """Calls are logged in UTC; spend is grouped by the person's own calendar day."""
    moment = datetime.fromisoformat(called_at)
    return moment.astimezone().date() if moment.tzinfo else moment.date()
