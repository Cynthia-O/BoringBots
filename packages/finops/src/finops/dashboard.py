"""The spend dashboard: a static botview page built from the ledger."""

from datetime import date

from botview import Badge, Chart, Page, Section, Series, Table, Text
from finops.ledger import Ledger
from finops.pricing import PRICES_CHECKED, usd


def dashboard_page(ledger: Ledger) -> Page:
    total = ledger.total
    days = sorted(ledger.days)
    sources = ", ".join(f"{s.bot} ({n:,} calls)" for s, _, n in ledger.found) or "none yet"
    facts = [
        ("Model calls", f"{total.calls:,}"),
        ("Input tokens", f"{total.input_tokens:,}"),
        ("Output tokens: response", f"{total.response_tokens:,}"),
        ("Output tokens: thinking", f"{total.thinking_tokens:,}"),
        ("Days", f"{days[0]:%b %-d, %Y} to {days[-1]:%b %-d, %Y}" if days else "none yet"),
        ("Logs read", sources),
    ]
    notices = []
    if total.unpriced:
        notices.append(
            Text(
                f"{total.unpriced} call(s) used a model with no price in finops/pricing.py and "
                "are left out of the dollar figures (their tokens are counted).",
                tone="warning",
            )
        )
    if total.thinking_unreported:
        notices.append(
            Text(
                f"{total.thinking_unreported} call(s) have no separate thinking count (logged "
                "before bots recorded it, or not reported by the API). Their thinking is "
                "counted as response tokens. Costs are unaffected: both are billed at the "
                "output rate.",
                tone="info",
            )
        )
    for source, path in ledger.missing:
        notices.append(Text(f"No call log for {source.bot} yet (looked in {path}).", tone="info"))

    if not days:
        sections = [Section("No spend yet", [], [Text("Nothing has been logged yet.")])]
    else:
        sections = [_charts(ledger, days), _daily_table(ledger, days), _breakdown(ledger)]
    sections.append(
        Section(
            "About these numbers",
            [],
            [
                Text(
                    "Tokens are as the API reported them in each bot's call log. Dollar "
                    "figures are estimated from those tokens; thinking is billed at the output "
                    "rate. Days follow this computer's time zone.",
                ),
                Text(
                    f"Prices last checked {date.fromisoformat(PRICES_CHECKED):%-d %B %Y}. Prices "
                    "change over time, so these are estimates at the standard API prices on that "
                    "date (packages/finops/src/finops/pricing.py). Your Claude Console invoice "
                    "is the final word.",
                    fine_print=True,
                ),
            ],
        )
    )
    return Page(
        title="BoringBots spend",
        subtitle="Every logged model call across all bots, by day.",
        hero=("Estimated spend to date", usd(total.cost.total)),
        facts=facts,
        notices=notices,
        sections=sections,
        searchable=False,
    )


def _labels(days: list[date]) -> list[str]:
    fmt = "%b %-d" if days[0].year == days[-1].year else "%b %-d, %Y"
    return [f"{d:{fmt}}" for d in days]


def _charts(ledger: Ledger, days: list[date]) -> Section:
    labels = _labels(days)
    daily_cost = [ledger.days[d].cost.total for d in days]
    running, cumulative = 0.0, []
    for value in daily_cost:
        running += value
        cumulative.append(running)
    return Section(
        "Spend over time",
        [],
        [
            Chart(
                "Estimated spend per day",
                "columns",
                labels,
                [Series("Estimated spend", daily_cost)],
                unit="usd",
            ),
            Chart(
                "Cumulative estimated spend",
                "line",
                labels,
                [Series("Cumulative spend", cumulative)],
                unit="usd",
            ),
            Chart(
                "Tokens per day",
                "columns",
                labels,
                [
                    Series("Input", [ledger.days[d].input_tokens for d in days]),
                    Series("Output: response", [ledger.days[d].response_tokens for d in days]),
                    Series("Output: thinking", [ledger.days[d].thinking_tokens for d in days]),
                ],
                subtitle="Output tokens are billed at a higher rate than input tokens.",
            ),
        ],
    )


def _daily_table(ledger: Ledger, days: list[date]) -> Section:
    rows = []
    cost_so_far = tokens_so_far = 0.0
    for d in days:
        t = ledger.days[d]
        cost_so_far += t.cost.total
        tokens_so_far += t.total_tokens
        if t.calls:  # quiet days stay in the charts but not in the table
            rows.append(
                [
                    f"{d:%a %b %-d, %Y}",
                    f"{t.calls:,}",
                    f"{t.input_tokens:,}",
                    f"{t.response_tokens:,}",
                    f"{t.thinking_tokens:,}",
                    usd(t.cost.total),
                    usd(cost_so_far),
                    f"{int(tokens_so_far):,}",
                ]
            )
    return Section(
        "By day",
        [Badge("same numbers as the charts")],
        [
            Table(
                [
                    "Day",
                    "Calls",
                    "Input tokens",
                    "Response tokens",
                    "Thinking tokens",
                    "Estimated spend",
                    "Cumulative spend",
                    "Cumulative tokens",
                ],
                rows,
                numeric=frozenset(range(1, 8)),
            )
        ],
    )


def _breakdown(ledger: Ledger) -> Section:
    rows = [
        [
            bot,
            model,
            f"{t.calls:,}",
            f"{t.input_tokens:,}",
            f"{t.output_tokens:,}",
            f"{t.thinking_tokens:,}",
            usd(t.cost.total) if not t.unpriced else "no price",
        ]
        for (bot, model), t in sorted(
            ledger.by_bot_model.items(), key=lambda item: -item[1].cost.total
        )
    ]
    return Section(
        "By bot and model",
        [],
        [
            Table(
                [
                    "Bot",
                    "Model",
                    "Calls",
                    "Input tokens",
                    "Output tokens",
                    "of which thinking",
                    "Estimated spend",
                ],
                rows,
                numeric=frozenset(range(2, 7)),
            )
        ],
    )
