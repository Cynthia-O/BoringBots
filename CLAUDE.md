# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

BoringBots is a collection of agents that do tedious tasks. Each bot's spec lives in `bots/<bot>/README.md`, which is the source of truth for its scope; keep it in sync when that scope changes. The bots are early skeletons: argparse CLIs with stubbed stages.

## Layout and commands

This is a uv workspace on Python 3.11+. The root `pyproject.toml` has no code of its own. It defines the workspace members (`bots/*`), the shared dev tools (pytest, ruff) and their settings, and one lockfile for the whole repo. Each bot under `bots/<bot>/` is its own package (`src/<bot>/`, `tests/`, `pyproject.toml`) with its own runtime dependencies and a console script named after the bot.

```sh
uv sync                                    # install all bots plus dev tools
uv run --package redundancybot redundancybot <dir>
uv run --package formbot formbot <form.pdf|form.docx>
uv run pytest                              # all tests
uv run pytest bots/formbot                 # one bot
uv run pytest bots/formbot/tests/test_cli.py::test_accepts_pdf   # one test
uv run ruff check . && uv run ruff format .
```

To add a bot, copy an existing bot's folder layout. It is picked up automatically through the `bots/*` glob. Add a bot's runtime dependencies to that bot's own `pyproject.toml`, not the root one. Only create shared code (`packages/botcommon`, then add `packages/*` to the workspace members) once two bots actually need the same code.

## Design rule shared by all bots: advise, never act

Every bot is human-in-the-loop. Bots analyze and recommend, and the user carries out every change. This is a hard limit on capability, not a UX preference. Do not give a bot tools that delete, move or edit the user's files or documents, even as an option.

## Planned bots

### RedundancyBot
Finds likely duplicate files and older versions so the user can decide what to delete. It works in three stages:
1. **Cluster (deterministic Python, `cluster.py`):** group files that may be related by name and extension. Clustering stays within a single file type and only covers types with more than one file in the corpus. Every file extension is in scope. Grouping by extension is implemented; name-similarity clustering is not yet.
2. **Analyze (agent):** within each cluster, suggest which files are older versions or convenience copies of other files.
3. **Explain (agent):** present the findings so the user can choose what to delete. The bot itself has no delete capability.

V1 sources: local files on the Mac and Google Drive.

### FormBot
Helps the user fill out a single PDF or Word form, which may be several pages long and mix text and images:
1. Extract the form's text and work out what each field is for.
2. Research unclear fields online. Cache the findings and their source links for the length of the user session (session-scoped, not persisted).
3. Walk the user through the form field by field, in conversation, giving text they can copy and paste. The bot never modifies the form itself.
