# finops
Spend tracking shared by all BoringBots.

- **`pricing.py`:** standard API prices per model, copied by hand from Anthropic's pricing page, with the date they were checked (`PRICES_CHECKED`). Update both together when prices change. `call_cost()` prices one call, splitting output into the visible response and thinking (both billed at the output rate).
- **`ledger.py`:** reads every bot's `llm_calls` log, read-only, and totals calls, tokens and estimated cost per day (in this computer's time zone), per bot and model. Each bot's log is listed in `SOURCES`; add a bot there when it starts logging calls.
- **`dashboard.py` and the `spend` command:** a static dashboard with a hero figure for total estimated spend, charts of spend per day, cumulative spend and tokens per day (input, response, thinking), a day-by-day table with cumulative columns, and a breakdown by bot and model.

```sh
uv run spend               # writes ~/.boringbots/dashboards/<time>_spend.html and opens it
uv run spend --no-browser  # writes it only
```

Each run writes a new file; nothing is overwritten, and the logs are never changed.
