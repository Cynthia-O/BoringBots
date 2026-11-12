# BoringBots
These back-office bots are terrible at parties.

## Install
What you need:
- **A Mac.** Linux should also work, but we haven't tested it. Windows isn't supported.
- **An internet connection** for the first install. Some bots also need it while they run, for example to reach Google Drive or to research form fields online.
- **No administrator access.** Nothing is installed system-wide, and you won't be asked for your password. uv and Python go into your home folder, and the bots go into a `.venv` folder inside this one. The uv installer adds one line to your shell profile so Terminal can find uv.
- **About 200 MB of disk space**, mostly for Python.
- **Permission for Terminal to read the folders you scan.** The first time a bot looks in a folder like Documents or Desktop, macOS may ask whether Terminal can access it. Click Allow, or the bot can't see those files.

On a Mac, open Terminal in this folder and run:

```sh
./install.sh
```

If you get "permission denied", run `sh install.sh` instead.

This installs [uv](https://docs.astral.sh/uv/) if you don't have it, which in turn fetches the right Python version and everything the bots need. Then run a bot from this folder, for example:

```sh
uv run dupebot ~/Documents
```

## Connect to Claude
Some bot features call Claude through the Anthropic API, which needs your own API key. Usage is billed to your account.

1. Create a key in the [Claude Console](https://platform.claude.com/).
2. In this folder, run `cp .env.example .env`.
3. Open `.env` in a text editor and paste your key after `ANTHROPIC_API_KEY=`.

`.env` is git-ignored: it stays on your computer and is never committed, so your key is never shared. Run the bots from this folder so they can find it. To try it out:

```sh
uv run dupebot ~/Documents --analyze
```

DupeBot tells you what it is about to send and asks before sending anything. The results open as a page in your browser. The page is a file on your computer and loads nothing from the internet.

To run test rounds from the browser instead, with settings controls (including the folder to scan) and a running tally of tokens and estimated cost:

```sh
uv run dupebot --ui
```

Press Ctrl+C in Terminal to stop it.

## Spend
To see what all the bots have spent on model calls, by day:

```sh
uv run spend
```

This opens a dashboard with your total estimated spend, charts of spend per day, cumulative spend and tokens per day, and a day-by-day table. It only reads the bots' call logs, and each run writes a new page under `~/.boringbots/dashboards/`. The dollar figures are estimates based on the prices in `packages/finops/src/finops/pricing.py`; your Claude Console invoice is the final word.

## [DupeBot](bots/dupebot/)
DupeBot helps you clear out clutter by finding files on your Mac and in Google Drive that are likely duplicates, older versions or convenience copies of one another. It groups candidate files by name and file type, then explains what it found so you can decide what to delete. It never deletes anything itself, so that decision always stays with you.

## [ExpiryBot](bots/expirybot/)
ExpiryBot takes a look at your data, suggests some retention groups (e.g., "tax documents and accessories, retain for 3-7 years"), and discusses those suggestions with you until you decide on a retention taxonomy. It then flags stale documents that you may want to delete. It can move the documents to a /deprecate/ subfolder if you ask it to, but cannot delete documents itself.

## [CatalogBot](bots/catalogbot/)
CatalogBot produces a README in every folder, describing what is contained in that folder and its subfolders. CatalogBot can quickly let you know what you have in your corpus.

## [TidyBot](bots/tidybot/)
TidyBot orchestrates DupeBot, ExpiryBot, and CatalogBot for corpus-wide clean-up jobs. TidyBot loves mess.

## [FormBot](bots/formbot/)
FormBot helps you fill out a PDF or Word form one field at a time. It reads the form, looks up anything unclear and shows its sources, then works through each field with you and gives you text to copy in. It never edits the form itself, so you stay in control of what goes on it.
