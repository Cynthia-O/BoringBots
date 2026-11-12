# DupeBot
This bot will have three jobs:
- Execute Python scripts to cluster potentially related or duplicate files by their name and file extension.
- Analyze those clusters to propose which files are older versions or convenience copies of other files.
- Explain what it has found to the end user to help that user select files to be deleted. The agent itself will not have the ability to delete the files, this will be the human's job.

From the BoringBots folder:

```sh
# browser app: choose folder and settings, scan, send, see suggestions
uv run dupebot --ui      
# print the cluster report as JSON; no model calls    
# ~/Desktop/ is only an example folder - replace as desired              
uv run dupebot ~/Desktop/     
# write the report to a new file instead                  
uv run dupebot ~/Desktop/ --output report.json  
# send clusters to the model and open the results page (asks first)
uv run dupebot ~/Desktop/ --analyze       
# open finops page (universal across all bots)
uv run spend   
```

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

The analyst model receives a trimmed copy of the report (`analyze.py`). It gets only the clusters and, for each file, its path (relative to the scanned folder, so the person's home folder name is never sent, and ending in the original file name), size, modified time and authors. Nothing describing how the clustering ran (`root`, `settings`, `summary`, `empty_after_cleaning`) is sent, and neither is anything our cleaning rules derived from the name (the cleaned name and the stripped dates and markers), so the model judges files by their original names alone. All of it is still kept locally. The prompt gives only a short account of the method, enough to recognise false matches.

The answer's format is enforced by the API (structured outputs, `OUTPUT_SCHEMA` in `analyze.py`), not described in the prompt. For each cluster the model returns groups; each group has one relationship (copies, version_series, variants, recurring, parts or unrelated), its reason, what to check before deleting, and its files, each with a role (current_version, earlier_version, original, copy or member). Earlier versions and copies are the files to consider deleting. Because a schema cannot check the answer against the input, DupeBot also checks that every file is placed exactly once and reports any that are left out, invented or doubled. The results will also be stored in a local SQLite table (not yet built) that keeps both the original and the cleaned name for every file.

### Suggestions agent (`suggest.py`)
After the analysis, an agent picks the clean-ups most worth your attention: specific files to consider deleting, each paired with the file to keep. This is DupeBot's first real agency: the model decides which checks to run and when it has seen enough. It has two read-only tools:
- `compare_files` checks whether two clustered files are byte-for-byte identical. The files are hashed on this Mac; only the yes/no answer and the sizes go to the API.
- `list_folder` lists a folder inside the scanned folder (names, sizes, modified dates), so the agent can see context such as an `old` folder or nearby versions. This can send names of files that were not in any cluster.

Limits:
- **Inside the scan only.** The tools resolve every path themselves, so `..`, absolute paths and links cannot reach outside the scan, and `compare_files` only accepts files from the clusters.
- **A cap on suggestions:** at most 25, or a quarter of the clusters analysed, whichever is smaller (rounded up, so at least 1). The cap is a ceiling: the agent may pick fewer. If the analysis flags no earlier versions or copies at all, the agent doesn't run.
- **A cap on turns:** 10 model requests. The last request has tools turned off, so the agent always ends with an answer.
- **A fixed answer format,** enforced by the API (structured outputs). The prompt is `suggest_prompt.md`.
- **Every suggestion is checked before it is shown.** Both files must be in the named cluster and must differ from each other. A file can't be suggested twice, a suggestion can't delete a file another one keeps (which could delete both copies), and suggestions over the cap are dropped. Dropped suggestions are listed on the page.
- **"Identical contents (checked)"** comes from DupeBot's own comparison results, never from the model's say-so.

Agent calls are logged with purpose `suggest`, under the same run as the analysis, so the token tally and cost cover both. The browser app runs the agent after every Send. When it finishes, the clusters it picked move to the top of the results page, in its order, each with a **priority** label and its suggestion (the file to consider deleting and the one to keep) above the cluster's analysis. Every other cluster follows below, so anything the agent passed over is on the same page, and a list of the checks the agent made comes last. `--analyze` in the terminal does not run the agent yet.

### Run settings (`settings.py`)
Before a run, DupeBot shows its settings with their defaults and asks "Keep these settings? [Y/n]". Enter keeps them all. Answering `n` goes through each in turn, still showing the default: the similarity threshold (0.6), then, with `--analyze`, the model (from a numbered list: Claude Haiku 5.5, Sonnet 5.5 or Opus 5.5; default Haiku) and the thinking effort (the model's own default, or low, medium, high, xhigh or max). A setting given as a flag (`--threshold`, `--model`, `--effort`) is shown but not asked about, and when flags cover every setting, nothing is asked. With no terminal to answer (for example in a script), the defaults are used.

### Sending to the model
`dupebot <dir> --analyze` sends the clusters to the chosen model and shows the analysis as a page in your browser, built with the shared `botview` package (`view.py`). Each run writes a new page to `~/.dupebot/pages/` (next to the call log), and the terminal gets a one-line summary per cluster. `--no-browser` saves the page without opening it. Whole clusters are packed into requests of up to 200 files; a cluster is never split. It states what will be sent and asks before sending (`--yes` skips the question). The API key comes from `ANTHROPIC_API_KEY`, normally set in the git-ignored `.env` at the repo root (template: `.env.example`).

### Browser app (`ui.py`)
Across the top, under the title, three numbered stages show where a round has got to: **1 Choose folder and settings**, **2 Analysis**, **3 Prioritization agent**. Completed stages are a saturated green, stages still to come a pale green, and the one in progress has a solid outline. Each stage carries a short live detail, such as clusters answered or what the agent is checking. A new scan resets stages 2 and 3. Saved copies of the results page have no stages, so they keep a notice that these are only suggestions, plus the agent's summary.

`dupebot <dir> --ui` runs DupeBot from a page in your browser, for test rounds without going back to the terminal. At the top are the settings: the folder to scan (a text field, starting on the folder given on the command line, if any; `~` works for your home folder), a slider for the similarity threshold, and toggles for the model and thinking effort. With `--ui` the folder argument is optional. Each control marks its default (dashed outline and a "default" tag). Flags such as `--threshold` preselect a control, and the default stays marked. Each round has two steps, so nothing is spent before you've seen what will be sent:
1. **Scan folder** clusters the folder at the chosen threshold and shows the clusters, including the cleaned name each file was matched as (local only; no API call).
2. **Send to model** sends exactly those clusters with the model and effort chosen at scan time, and the results page builds up as you watch:
   - **Analysis:** every cluster starts as a "waiting" placeholder and fills in, top to bottom, as the model writes its answer for it. The answer streams in, and each cluster is shown as soon as its part is complete. The status line counts clusters answered.
   - **Prioritization:** the status line shows the agent's turn (of at most 10), its checks so far, and what it is checking right now, with a seconds ticker.
   - **Done:** the agent's picks move to the top and the rest stay below (see "Suggestions agent"). The final page is also saved in `~/.dupebot/pages/`.

At the bottom, a tally shows tokens and estimated cost for input, output split into the visible response and hidden thinking (from the API's `output_tokens_details.thinking_tokens`), billed output and the total, plus the number of API calls, for the whole session and for the last run. Costs are estimated with the shared `finops` package (`packages/finops/src/finops/pricing.py`), which holds standard API prices copied by hand from Anthropic's pricing page (with the date they were checked). Update it when prices change. Each call is priced on its own, because Haiku 5.5 charges more for a request whose prompt is over 100K tokens. Results pages and the terminal output of `--analyze` show the run's estimated cost too. It is counted from the call log, so billed calls whose answers were cut off or refused are included. The session ends when you stop DupeBot with Ctrl+C, and the tally starts at zero on the next start.

Every file row, on the clusters page and on the results page, has a **Show in Finder** button that opens the file's folder in a new Finder window with the file selected, so you can look at it or compare folders side by side before deciding anything. It is read-only. It works by asking Finder (via a short AppleScript) to open a window and select the file, so macOS asks once whether the app running DupeBot (e.g. Visual Studio Code or Terminal) may control Finder. If that is refused, DupeBot falls back to `open -R`, which needs no permission but may reuse one window, and says how to turn the permission on (System Settings > Privacy & Security > Automation). The button carries an id issued by this session's scan, never a path, and DupeBot only acts on ids it issued, for files that are still there. Files the model names that were not in the scan get no button. Saved copies of pages don't show the buttons, since there is no app behind them.

The page is served only on this machine (127.0.0.1) and needs a random key minted for each session, so other websites open in your browser cannot drive it or start paid calls.

### LLM call log (`calllog.py`)
Every LLM call, including failed ones, adds a row to the `llm_calls` table in `~/.dupebot/dupebot.sqlite` (override with `DUPEBOT_DB`), for FinOps and LLMOps tracking. The database lives outside the repo so nothing in it can be committed. Each row records the run, time (UTC), purpose, model requested and model that answered, thinking effort requested (empty means the model's default; set it with `--effort low|medium|high|xhigh|max`), a fingerprint of the system prompt, input and output characters, input and output tokens (and how many of the output tokens were thinking), duration, stop reason, any error, and the API request id. Rows are only ever added; when a newer DupeBot adds a column, older rows are kept with it empty.

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
