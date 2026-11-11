"""Write a rendered page to a new file and open it in the browser.

Pages are always new files: an existing file is never written over, in line
with the BoringBots rule that bots create but never edit.
"""

import re
import webbrowser
from datetime import datetime
from pathlib import Path

from botview.page import Page, render


def write_page(page: Page, directory: Path, name: str | None = None) -> Path:
    """Write page into directory as a new, timestamped HTML file and return its path.

    name (default: the page title) becomes part of the file name. If a file of
    that name exists, a number is added rather than writing over it.
    """
    directory.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y-%m-%d_%H%M%S}_{_slug(name or page.title)}"
    text = render(page)
    n = 1
    while True:
        path = directory / (f"{stem}.html" if n == 1 else f"{stem}-{n}.html")
        try:
            with path.open("x", encoding="utf-8") as f:  # "x" never overwrites
                f.write(text)
            return path
        except FileExistsError:
            n += 1


def open_in_browser(path: Path) -> bool:
    """Open a page in the default browser. Returns False if no browser could be found."""
    return webbrowser.open(path.resolve().as_uri())


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60] or "page"
