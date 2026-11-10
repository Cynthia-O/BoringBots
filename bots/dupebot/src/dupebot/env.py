"""Load settings such as ANTHROPIC_API_KEY from a local, git-ignored .env file."""

import os
from pathlib import Path


def load_dotenv(start: Path | None = None) -> Path | None:
    """Load KEY=VALUE lines from the nearest .env in start (default: the current
    folder) or any folder above it. Returns the file loaded, if any.

    Variables already set in the environment win over the file, so a key
    exported in the shell is never replaced.
    """
    directory = (start or Path.cwd()).resolve()
    for folder in (directory, *directory.parents):
        path = folder / ".env"
        if path.is_file():
            for raw in path.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, value = line.partition("=")
                key = key.removeprefix("export ").strip()
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if key and value:
                    os.environ.setdefault(key, value)
            return path
    return None
