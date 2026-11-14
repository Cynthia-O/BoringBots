# ExpiryBot
ExpiryBot looks at a folder of your documents, suggests retention groups (for example "tax returns and supporting documents, keep 7 years"), and works through them with you until you settle on a retention taxonomy. For each group you then choose which date a document's age is counted from. Finally it flags the documents that are past the retention period you chose, so that you can delete them. It never deletes, moves, renames or edits anything itself.

Like DupeBot, ExpiryBot does everything it can in plain Python, which is free and gives the same answer every time. A model is used only where judgement is needed: proposing the groups, and revising them when you ask. Everything else is deterministic: reading the folder, sorting files into groups, comparing dates and flagging expired files.

Status: stages 1 (survey) and 5 (flag) work, against a taxonomy written by hand. Stages 2 to 4 and the browser app are not built yet, so ExpiryBot makes no model calls.

From the BoringBots folder:

```sh
# survey a folder: what it holds and which dates its files carry (free, read-only)
uv run expirybot ~/Documents/
# flag it against a taxonomy (copy and edit examples/taxonomy.json)
uv run expirybot ~/Documents/ --taxonomy my-taxonomy.json
# try it as if it were another day, list files expiring within 180 days as "soon",
# and write the expired files to a new CSV (or .json) file
uv run expirybot ~/Documents/ --taxonomy my-taxonomy.json --today 2027-01-01 --soon-days 180 --expired-list expired.csv
```

Each run writes a new page to `~/.expirybot/pages/` (set `EXPIRYBOT_HOME` to use another folder) and opens it; `--no-browser` only saves it.

## Stages

### 1. Survey (Python, no LLM)
ExpiryBot walks the folder and records these facts for each file:
- path, extension, size;
- **modified time** and **created time** (from the filesystem; macOS keeps a creation time);
- **the latest date in the name** (2023-01-15, March 2023, a bare tax year such as 2021), found by the same rules DupeBot uses (the shared `filefacts` package);
- **the latest date in the nearest dated folder name** (`Taxes/2021/`, `Statements 2023-03/`);
- **dates stored inside the file**, where the format has them: the created and modified dates in Office and OpenDocument metadata, and in a PDF's document information.

A date in a name is read as the latest day it can mean, so retention counted from it can only end later, never earlier: a month counts as its last day, a bare year as 31 December, and `01-02-2023` (2 January or 1 February) as 1 February.

System clutter (.DS_Store, Office lock files and so on) is skipped and counted. Symlinks are not followed.

**Hidden folders** (names starting with a dot, such as `.git`, `.venv`, `.ssh` or a folder someone hid on purpose) are surveyed like any other folder, not skipped. They get a place in the taxonomy like everything else, even if that place ends up being "underlying files we never delete". The survey and flag pages list each hidden folder (the outermost one on each path) with its file count and size, and the flag page shows which groups its files landed in, so none is overlooked. No file is opened except to read the dates stored inside it.

Without a taxonomy, ExpiryBot shows the survey as a page: how many files carry each kind of date, and a "Folders at a glance" chart. Its top bar spans the whole folder's modified dates (on an axis padded 5% each side). Below it, each top-level folder, most files first and 10 per page, gets its own date bar and a 100% bar of its file types. A last bar shows the file types of all files. The six commonest types across the whole folder get their own colours and the rest are "Other"; the types, colours and date axis stay the same from page to page. A table below gives the same facts as text. These are the facts you need to write a taxonomy (and, later, what the model is given).

The survey also builds a **summary** for the model: the folder tree with file counts, counts by extension, common name patterns (names with dates and numbers set aside) and a capped sample of real names for each pattern. The model gets the summary, not the whole file list, so the cost of a run depends little on the size of the folder.

### 2. Propose groups (one LLM call)
The model reads the summary and proposes retention groups. The answer's format is fixed by the API (structured outputs). Each group has:
- a name and a short description;
- **match rules**: which files belong, written as folder names, words in the name and extensions, so that Python can apply them;
- a suggested retention period, either as a range with its reasoning (for example 3 to 7 years for tax records, with when each end applies) or as "keep forever";
- optional subgroups, which inherit anything they don't set themselves.

Python then applies the rules to every file and shows, for each group, how many files it catches, with examples. It also lists files caught by more than one group (groups are ordered, and the first match wins) and files that no group catches. The proposal is a starting point, and the next stage is where you settle it.

### 3. Settle the taxonomy (rounds, mostly free)
In the browser you edit the groups directly: rename, merge, split, reorder or remove them, change their rules, and set **one retention period per group** (flagging needs a number, so a range has to become a single figure). Each edit re-sorts the files in Python immediately, at no cost.

Two things cost money, and each asks first:
- **Ask the model to revise.** Send the current groups, their match counts and your comments, and get back a revised proposal. This is one call.
- **Sort the leftovers.** Send files that no rule catches to the model, which places each one in a group or leaves it out. The number of files sent per request is capped and shown before you confirm.

Each time you save the taxonomy, it is written as a new, timestamped JSON file under `~/.expirybot/taxonomies/`. Earlier versions are never overwritten.

### 4. Choose each group's age date (Python, with you)
No single date suits every kind of document. For tax forms the modified time is often the best guide, while a statement may be best dated by the date in its name. So the choice is made **for each group, once the groups are settled**, and subgroups inherit their parent's choice unless you change it.

For each group, ExpiryBot compares the candidate date sources across that group's files. In a taxonomy file they are named:
- `modified`: modified time on this disk;
- `created`: created time on this disk;
- `name`: date in the file name;
- `folder`: date in a folder name;
- `inside_modified` and `inside_created`: dates stored inside the file.

For each source it shows how many of the group's files have one, how often it agrees with the others, how many files would be expired if that source were used, and example files where the sources disagree most. You pick a first choice and a fallback order for files that lack it. Modified time is the starting suggestion, because it is present for every file. All of this is plain Python, and no model is involved. The choices are saved with the taxonomy.

Until stage 4 is built, you set the order by hand as each group's `age_date` (see "Taxonomy file" below), and the survey page shows how many files carry each source.

### 5. Flag (Python, no LLM)
Using the taxonomy, each group's date choice and today's date (or `--today`), ExpiryBot sorts every file into one of five lists: **expired**, **expiring soon** (within `--soon-days`, default 90), **kept** (including groups kept forever), **no usable date** (the file has none of the sources its group lists; add `modified`, which every file has, as a fallback) or **not in any group**. A file kept 7 years from 15 April 2018 is still kept on 15 April 2025 and expired from the next day. Each file shows its group, the date used and where that date came from, and how far past (or short of) its retention period it is. Each file also gets a Show in Finder button, as in DupeBot.

ExpiryBot can also write the expired list to a new file (JSON or CSV, `--expired-list`), for example so that a RAG index or an agent's context can exclude those files. It never writes over an existing file. Once the taxonomy is saved, a flagging run costs nothing and can be repeated whenever you like.

## Taxonomy file
Until stages 2 and 3 are built, the taxonomy is a JSON file you write; [examples/taxonomy.json](examples/taxonomy.json) is a starting point (its retention periods are placeholders, not advice). ExpiryBot checks the whole file before flagging and lists every problem it finds, such as a misspelt key or an unknown date source.

```json
{
  "name": "Home documents",
  "groups": [
    {
      "name": "Tax",
      "match": {"folders": ["tax"]},
      "keep_years": 7,
      "age_date": ["modified", "name"],
      "subgroups": [
        {"name": "Returns", "match": {"words": ["1040", "return"]}, "keep_years": "forever"}
      ]
    }
  ]
}
```

- **Matching:** a group's `match` can give `folders` (looked for in each folder name on the file's path), `words` (looked for in the file name without its extension) and `extensions`. Folders and words ignore case and match anywhere in a name, so `tax` matches `Taxes 2021` (and also `Syntax`; the page's counts show such surprises). Every rule a group gives must hold, and within a rule any one term is enough.
- **Order:** groups are tried from the top, and the first match wins. A file in a group is then tried against its subgroups the same way, and stays in the group if none matches.
- **Retention:** `keep_years` is a whole number of years or `"forever"`. Every top-level group needs one.
- **Age date:** `age_date` lists date sources in the order to try them. Subgroups inherit `keep_years` and `age_date` when they leave them out, and a top-level group without `age_date` uses `modified`.

## Spend, tracing and experiments
These follow DupeBot's patterns and the shared packages:
- **Spend:** nothing is sent to a model without a confirm step that says what will be sent. Haiku is the default model. Each call is logged to an append-only `llm_calls` table in `~/.expirybot/expirybot.sqlite`, which is listed in `finops/ledger.py` `SOURCES` so that `uv run spend` covers it. The browser app shows a running tally of tokens and cost.
- **Privacy:** paths sent to the model are relative to the scanned folder. Only the summary, your comments and capped samples of names are sent, never file contents.
- **Tracing:** each click or command is a bottrace trace, with spans for the survey, matching, each model call and flagging (built so far: survey, flag, the expired list and the page). The call log's `run_id` is the trace id.
- **Experiments:** prompts live in `.md` files, and each call logs a fingerprint of its prompt. The settings (model, effort, sample size per pattern, the expiring-soon window) show their defaults, and each browser window runs separately, so you can compare models side by side.
- **Interface:** a botview live app, with stages across the top: Survey, Groups, Age dates, Flag.

## Not built
- **Moving expired files to a `/deprecate/` subfolder.** The original idea was to keep sentimental files while excluding them from a RAG knowledge base. A move would break the rule that bots never move, rename, edit or delete your files. The expired-list file in stage 5 serves the same purpose without moving anything. Revisit only if that rule is deliberately changed.
