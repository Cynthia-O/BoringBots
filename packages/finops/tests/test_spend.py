import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from botview import render
from finops import cli
from finops.dashboard import dashboard_page
from finops.ledger import Source, build_ledger


def make_log(path, rows):
    """A call log shaped like a bot's llm_calls table."""
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE llm_calls (id INTEGER PRIMARY KEY, called_at TEXT, model TEXT, "
        "input_tokens INTEGER, output_tokens INTEGER, thinking_tokens INTEGER)"
    )
    conn.executemany(
        "INSERT INTO llm_calls (called_at, model, input_tokens, output_tokens, thinking_tokens) "
        "VALUES (?, ?, ?, ?, ?)",
        rows,
    )
    conn.commit()
    conn.close()


def at(day: date, hour: int = 12) -> str:
    """Noon local time on day, written in UTC as the bots log it."""
    local = datetime(day.year, day.month, day.day, hour).astimezone()
    return local.astimezone(UTC).isoformat(timespec="seconds")


D1 = date(2026, 11, 8)
D3 = date(2026, 11, 10)


@pytest.fixture
def source(tmp_path, monkeypatch):
    path = tmp_path / "bot.sqlite"
    make_log(
        path,
        [
            (at(D1), "claude-haiku-5-5", 1_000_000, 200_000, 100_000),
            (at(D3), "claude-sonnet-5-5", 100_000, 10_000, None),
            (at(D3), "some-unknown-model", 50, 50, 0),
            (at(D3), "claude-haiku-5-5", None, None, None),  # a failed call: no tokens
        ],
    )
    monkeypatch.setenv("TEST_BOT_DB", str(path))
    return Source("TestBot", "TEST_BOT_DB", tmp_path / "unused.sqlite")


def test_ledger_totals_by_day_with_quiet_days_filled(source):
    ledger = build_ledger([source])
    assert list(ledger.days) == [D1, D1 + timedelta(1), D3]
    assert ledger.days[D1 + timedelta(1)].calls == 0
    d1 = ledger.days[D1]
    assert (d1.input_tokens, d1.response_tokens, d1.thinking_tokens) == (
        1_000_000,
        100_000,
        100_000,
    )
    # Haiku over 100K-token prompt rate: $0.50 in, $2.50 out per million
    assert d1.cost.total == pytest.approx(0.50 + 0.50)
    assert ledger.days[D3].cost.total == pytest.approx(0.20 + 0.10)
    assert ledger.total.calls == 4 and ledger.total.unpriced == 1
    assert ledger.total.thinking_unreported == 1  # the Sonnet call had no thinking count


def test_ledger_never_changes_the_log(source):
    path = source.path()
    before = path.read_bytes()
    build_ledger([source])
    assert path.read_bytes() == before


def test_missing_logs_are_reported_not_fatal(tmp_path):
    ledger = build_ledger([Source("Nobody", "NO_SUCH_ENV_VAR_X", tmp_path / "none.sqlite")])
    assert ledger.days == {} and len(ledger.missing) == 1
    html = render(dashboard_page(ledger))
    assert "No call log for Nobody yet" in html and "$0.00" in html


def test_dashboard_shows_hero_charts_tables_and_fine_print(source):
    html = render(dashboard_page(build_ledger([source])))
    assert "Estimated spend to date" in html and "$1.30" in html
    for title in ("Estimated spend per day", "Cumulative estimated spend", "Tokens per day"):
        assert title in html
    assert html.count('class="chart-hit"') == 9  # three charts x three days
    assert "Cumulative spend" in html and "By bot and model" in html
    assert "no price" in html and "Prices last checked" in html


def test_spend_command_writes_a_new_dashboard(source, tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "SOURCES", [source], raising=False)
    monkeypatch.setattr("finops.ledger.SOURCES", (source,))
    monkeypatch.setattr(cli, "DASHBOARD_DIR", tmp_path / "dash")
    opened = []
    monkeypatch.setattr(cli, "open_in_browser", lambda p: opened.append(p) or True)
    assert cli.main([]) == 0
    assert cli.main(["--no-browser"]) == 0
    files = sorted((tmp_path / "dash").glob("*_spend*.html"))
    assert len(files) == 2 and len(opened) == 1
