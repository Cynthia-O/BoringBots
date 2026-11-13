# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

BoringBots is a collection of agents that do tedious tasks. Each bot's spec lives in `bots/<bot>/README.md`, which is the source of truth for its scope; keep it in sync when that scope changes. The bots are early skeletons: argparse CLIs with stubbed stages.

## Layout and commands

This is a uv workspace on Python 3.11+. The root `pyproject.toml` has no code of its own. It defines the workspace members (`bots/*`), the shared dev tools (pytest, ruff) and their settings, and one lockfile for the whole repo. Each bot under `bots/<bot>/` is its own package (`src/<bot>/`, `tests/`, `pyproject.toml`) with its own runtime dependencies and a console script named after the bot.

```sh
./install.sh                               # first-time setup: installs uv if missing, then syncs
uv sync --all-packages                     # install all bots plus dev tools
uv run --package dupebot dupebot <dir>
uv run --package dupebot dupebot <dir> --analyze   # calls the API; key in .env (copy .env.example)
uv run --package dupebot dupebot [<dir>] --ui      # browser app: folder, settings, scan, send, token/cost tally
uv run --package formbot formbot <form.pdf|form.docx>
uv run spend                               # spend dashboard across all bots (finops)
uv run traces                              # request traces across all bots (bottrace)
uv run --package expirybot expirybot <dir>   # likewise catalogbot, tidybot (stubs for now)
uv run pytest                              # all tests
uv run pytest bots/formbot                 # one bot (or packages/botview)
uv run pytest bots/formbot/tests/test_cli.py::test_accepts_pdf   # one test
uv run ruff check . && uv run ruff format .
```

To add a bot, copy an existing bot's folder layout. It is picked up automatically through the `bots/*` glob. Add a bot's runtime dependencies to that bot's own `pyproject.toml`, not the root one. Code shared between bots lives in `packages/*` (also workspace members); a bot depends on a package by listing it in `dependencies` plus `[tool.uv.sources] <pkg> = { workspace = true }`. Otherwise, only move code into a shared package once two bots actually need it.

**Spend tracking (`packages/finops`):** model prices (`pricing.py`, checked by hand; update `PRICES_CHECKED` with them) and the `spend` dashboard, which reads every bot's `llm_calls` log read-only. A bot that calls a model logs each call to its own append-only `llm_calls` table (see `dupebot/calllog.py`) and is listed in `finops/ledger.py` `SOURCES`.

**Tracing (`packages/bottrace`):** every human request (a browser click, a command run) is one trace of nested spans (stage, llm, turn, tool, wait) in the local `~/.boringbots/traces.sqlite`. Bots start a trace with `Tracer("Bot").trace(...)` and mark steps with `span(...)` or `@traced`, which do nothing outside a trace. Model calls should record tokens, cost and time to first token (see `dupebot.analyze.stream_and_log`); use the trace id as the call log's `run_id` so the two join.

**Shared browser UI (`packages/botview`):** every bot shows results to the user in a local browser page built with botview, not as tables in the terminal. A bot describes the page as data (`Page`, `Section`, `Card`, `Table`, `Text`, `Badge`); botview renders a self-contained HTML file (no web requests), writes it as a new file, and opens it. Keep bot-specific content in the bot (e.g. `dupebot/view.py`) and keep botview generic. For interactive use, botview also runs a live app (`LiveServer`): a local page with settings controls (`Choice`, `Slider`) whose actions call the bot's `handle()`; see `dupebot/ui.py`. Anything that spends money goes through a `Confirm` step first.

## Design rule shared by all bots: create, but never edit or delete

No bot may edit or delete the user's existing files or documents. Bots may create new files, such as reports or the per-folder READMEs CatalogBot writes. This is a hard limit on capability, not a UX preference: any change to existing content is the user's job, so do not give a bot tools that edit or delete, even as an option. This means:
- **Never overwrite.** Writing over a file that already exists counts as editing it. If a target file exists, skip it or write to a new name.
- **Never move or rename.** Either one removes the file from its original location.
- **Showing is fine.** Opening a file's folder in Finder (DupeBot's Show in Finder: an AppleScript that opens a new window and selects the file, falling back to `open -R`) changes nothing and is allowed. A person-clicked "Move to Trash" button was discussed (2026-11-10) and not built; it would need a deliberate change to this rule first.

## Planned bots

### DupeBot
Finds likely duplicate files and older versions so the user can decide what to delete. It works in three stages:
1. **Cluster (deterministic Python):** group files that may be related by name and extension. Clustering stays within a single file type and only covers types with more than one file in the corpus. Every file extension is in scope. Implemented: `cleaning.py` cleans names for matching (keeping the original and recording what was stripped), `cluster.py` buckets by cleaned-name prefix and clusters by trigram Jaccard similarity in parallel, and `report.py` builds the JSON handed to the agent stages. Details are in the bot's README.
2. **Analyze (LLM call):** within each cluster, label which files are older versions or convenience copies of other files (`analyze.py`, one structured call per batch, no tools). Then **suggest (agent, `suggest.py`):** a tool-using loop with read-only tools (`compare_files`, `list_folder`, both confined to the scan) picks the clean-ups worth attention, capped at min(25, 25% of clusters analysed); Python validates every suggestion before showing it.
3. **Explain (agent):** present the findings so the user can choose what to delete. The bot itself has no delete capability.

V1 sources: local files on the Mac and Google Drive.

### ExpiryBot
Suggests retention groups for the user's documents (for example, tax documents kept for 3 to 7 years) and discusses them with the user until they settle on a retention taxonomy. It then flags documents past their retention period so the user can delete them. **Unresolved:** the spec says it can move documents to a `/deprecate/` subfolder on request, which conflicts with the no-move rule above. Do not build the move feature until the user resolves this.

### CatalogBot
Writes a README in each folder describing what that folder and its subfolders contain, giving a quick overview of the corpus. Under the design rule, it must skip any folder that already has a README rather than overwrite it.

### TidyBot
Orchestrates DupeBot, ExpiryBot and CatalogBot for corpus-wide clean-up jobs. It is bound by the same rule as the bots it runs.

### FormBot
Helps the user fill out a single PDF or Word form, which may be several pages long and mix text and images:
1. Extract the form's text and work out what each field is for.
2. Research unclear fields online. Cache the findings and their source links for the length of the user session (session-scoped, not persisted).
3. Walk the user through the form field by field, in conversation, giving text they can copy and paste. The bot never modifies the form itself.
