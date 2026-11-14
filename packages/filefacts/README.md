# filefacts
Read-only facts about files, shared by DupeBot and ExpiryBot. Nothing here writes, moves or deletes anything.

- **`namedates.py`:** dates written in file and folder names. `DATE_RE` finds them (DupeBot strips them from names before clustering); `find_dates` and `latest_date` also read them as calendar dates. Where a name leaves room for doubt, the latest date it can mean is used: a month counts as its last day, a bare year (1990-2039) as 31 December, and `01-02-2023` as 1 February. Other numbers are not dates, so `Form 1040` is left alone.
- **`embedded.py`:** what some formats store inside the file. `read_authors` gives the creator and last modifier (Office, OpenDocument); `read_dates` gives the created and modified dates (Office, OpenDocument, PDF via `pypdf`). Placeholder dates before 1985 and damaged files give empty values instead of errors.
- **`clutter.py`:** `is_clutter` spots system files (.DS_Store, Thumbs.db) and office lock files.
