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
4. **Excluded names** are never clustered: system clutter (.DS_Store, Thumbs.db, Office lock files), camera, screenshot and scanner names (IMG_1234, Screenshot ..., Scan 12), and generic titles once cleaned (index, readme, untitled, document, notes, ...). The lists live at the top of `cleaning.py` and are meant to grow.
5. **Minimum size** is 250 KB, so related files differ enough in size for later dupe rules.

Names that are empty after cleaning are skipped and listed in the report.

### Clustering (`cluster.py`)
1. Group by extension, dropping extensions with only one file.
2. Bucket by the first 5 characters of the cleaned name. Any bucket over 1000 files is split again on one more character, until it fits.
3. Within each bucket, link files whose cleaned names have a Jaccard similarity of character trigrams at or above the threshold; each connected group is a cluster. The threshold defaults to 0.6, and the CLI asks the user whether to change it (or takes `--threshold`).
4. Buckets are processed in parallel, one bucket per core, using one core fewer than the machine has. Small jobs run in one process, because starting workers would cost more than it saves.

### Handoff to the LLM (`report.py`)
A JSON report: settings, summary counts, skipped counts, and one entry per cluster with its id, extension and files (oldest first). Each file carries its path, original name, cleaned name, size, modified time, and the dates, version markers and set markers stripped from it. It is printed to stdout, or written with `--output` to a new file; DupeBot refuses to write over an existing file.
