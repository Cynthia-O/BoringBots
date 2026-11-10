# DupeBot
This bot will have three jobs:
- Execute Python scripts to cluster potentially related or duplicate files by their name and file extension.
- Analyze those clusters to propose which files are older versions or convenience copies of other files.
- Explain what it has found to the end user to help that user select files to be deleted. The agent itself will not have the ability to delete the files, this will be the human's job.

Initial scope:
- Local Mac-hosted files and Google Drive for now V1.
- All file extensions. Cluster within filetype, for any filetype that has more than one representative in the corpus.

## Stage 1: clustering (Python, no LLM)

### Name clean-up (`cleaning.py`)
Each file name is cleaned for matching only. Nothing is thrown away: the full original name is kept, and everything stripped is recorded per file so the LLM and the user can see it.
1. **Dates** are stripped from anywhere in the name (2023-01-15, 20230115, 01.15.2023, Jan 15 2023, March 2023, bare years 1990-2039). Other bare numbers are kept.
2. **Version-series markers** are stripped from the start and end of the name, at word boundaries: words like FINAL, draft, edits, redline, copy; version numbers like v2, v1.0, 1.0, rev3, "version 2"; phrases like co_edits, track changes, "Copy of"; copy numbers like "(1)"; and a trailing 2-3 letter token joined by `_` or `-`, taken to be initials (`_jb`).
3. **Version-set markers** are stripped from the start and end: regions and languages (USA, EMEA, German; two-letter codes like `_DE` only when written in capitals), audiences (internal, public, print) and "for ..." phrases in the last three words (`_for_executives`). These are recorded separately from version-series markers so the LLM can treat them differently.
4. **Excluded names** are never clustered: system clutter (.DS_Store, Thumbs.db, Office lock files), camera, screenshot and scanner names (IMG_1234, 20261012_142703, Screenshot ..., Scan 12), and generic titles once cleaned (index, readme, untitled, document, notes, ...). The lists live at the top of `cleaning.py` and are meant to grow.
5. **Minimum size** is 250 KB for images and 20 KB for everything else, so related files differ enough in size for later dupe rules. Images get the higher floor because small ones are mostly icons and thumbnails, while bills, receipts and similar documents are often well under 100 KB.

Names that are empty after cleaning are skipped and listed in the report.

### Clustering (`cluster.py`)
1. Group by extension, dropping extensions with only one file.
2. Bucket by the first 5 characters of the cleaned name. Any bucket over 500 files is split again on one more character, until it fits.
3. Within each bucket, link files whose cleaned names have a Jaccard similarity of character trigrams at or above the threshold; each connected group is a cluster. The threshold defaults to 0.6 (see "Run settings" below).
4. Buckets are processed in parallel, one bucket per core, using one core fewer than the machine has. Small jobs run in one process, because starting workers would cost more than it saves.

### Handoff to the LLM (`report.py`)
A JSON report: settings, summary counts, skipped counts, and one entry per cluster with its id, extension and files (oldest first). Each file carries its path, original name, cleaned name, size, modified time, created-by and modified-by (`metadata.py`; read only for clustered files, and only from formats that store them: Word, Excel, PowerPoint and OpenDocument), and the dates, version markers and set markers stripped from it. It is printed to stdout, or written with `--output` to a new file; DupeBot refuses to write over an existing file.

## Stage 2: analysis (LLM)

The analyst model receives a trimmed copy of the report (`analyze.py`). It gets only the clusters and, for each file, its path (relative to the scanned folder, so the person's home folder name is never sent, and ending in the original file name), size, modified time and authors. Nothing describing how the clustering ran (`root`, `settings`, `summary`, `empty_after_cleaning`) is sent, and neither is anything our cleaning rules derived from the name (the cleaned name and the stripped dates and markers), so the model judges files by their original names alone. All of it is still kept locally. The prompt gives only a short account of the method, enough to recognise false matches. The analysis results will be stored in a local SQLite table (not yet built) that keeps both the original and the cleaned name for every file.

### Run settings (`settings.py`)
Before a run, DupeBot shows its settings with their defaults and asks "Keep these settings? [Y/n]". Enter keeps them all. Answering `n` goes through each in turn, still showing the default: the similarity threshold (0.6), then, with `--analyze`, the model (from a numbered list: Claude Haiku 5.5, Sonnet 5.5 or Opus 5.5; default Haiku) and the thinking effort (the model's own default, or low, medium, high, xhigh or max). A setting given as a flag (`--threshold`, `--model`, `--effort`) is shown but not asked about, and when flags cover every setting, nothing is asked. With no terminal to answer (for example in a script), the defaults are used.

### Sending to the model
`dupebot <dir> --analyze` sends the clusters to the chosen model and prints the analysis. Whole clusters are packed into requests of up to 200 files; a cluster is never split. It states what will be sent and asks before sending (`--yes` skips the question). The API key comes from `ANTHROPIC_API_KEY`, normally set in the git-ignored `.env` at the repo root (template: `.env.example`). The output is free text for now; a fixed, parseable format comes next.

### LLM call log (`calllog.py`)
Every LLM call, including failed ones, adds a row to the `llm_calls` table in `~/.dupebot/dupebot.sqlite` (override with `DUPEBOT_DB`), for FinOps and LLMOps tracking. The database lives outside the repo so nothing in it can be committed. Each row records the run, time (UTC), purpose, model requested and model that answered, thinking effort requested (empty means the model's default; set it with `--effort low|medium|high|xhigh|max`), a fingerprint of the system prompt, input and output characters, input and output tokens, duration, stop reason, any error, and the API request id. Rows are only ever added.

To look at it, use `sqlite3`, which comes with macOS. Every call, the most useful columns:

```sh
sqlite3 -header -column ~/.dupebot/dupebot.sqlite "SELECT called_at, model, input_chars, output_chars, input_tokens, output_tokens, duration_ms, stop_reason FROM llm_calls"
```

The full table, every row and every column. The table is wide, so `-line` prints each call as a block of `column = value` lines rather than one long row:

```sh
sqlite3 -line ~/.dupebot/dupebot.sqlite "SELECT * FROM llm_calls"
```

To browse it in a spreadsheet, export it to CSV and open that in Numbers or Excel. This writes a new file, replacing any `llm_calls.csv` already on your Desktop:

```sh
sqlite3 -csv -header ~/.dupebot/dupebot.sqlite "SELECT * FROM llm_calls" > ~/Desktop/llm_calls.csv
```

To compare what you pay for with what you get back, by model and effort. `output_tokens` is billed and includes thinking; `output_chars` is only the visible text. Plain English runs at roughly 4 characters per token, so the lower `chars_per_token` falls, the more of the output went on thinking:

```sh
sqlite3 -header -column ~/.dupebot/dupebot.sqlite "SELECT model, COALESCE(effort, 'default') AS effort, COUNT(*) AS calls, SUM(output_tokens) AS output_tokens, SUM(output_chars) AS output_chars, ROUND(1.0 * SUM(output_chars) / SUM(output_tokens), 2) AS chars_per_token FROM llm_calls WHERE error IS NULL GROUP BY model, effort"
```

For a point-and-click view, [DB Browser for SQLite](https://sqlitebrowser.org/) (free) opens the database file directly. Browse it read-only, since the log is meant to be append-only.
