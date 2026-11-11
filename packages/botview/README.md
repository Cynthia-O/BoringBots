# botview
Shared browser pages for every BoringBot. A bot describes what to show as data, and botview turns it into a page in the user's browser, so all the bots look and behave alike and none of them writes HTML.

```python
from botview import Badge, Card, Page, Section, Table, Text, open_in_browser, write_page

page = Page(
    title="DupeBot results",
    facts=[("Model", "claude-haiku-5-5"), ("Clusters", "12")],
    sections=[
        Section(
            "pdf-0001",
            badges=[Badge("high confidence", "good")],
            blocks=[
                Card(
                    "Version series",
                    blocks=[
                        Text("v3 is newest and largest."),
                        Table(
                            ["File", "Size"], [["report_v3.pdf", "2.1 MB"]], numeric=frozenset({1})
                        ),
                    ],
                ),
            ],
        ),
    ],
)
path = write_page(page, Path.home() / ".mybot" / "pages")
open_in_browser(path)
```

- **Blocks:** `Text` (plain, or a coloured callout with a tone and an optional bold label), `Table` (optional tone per row and right-aligned number columns) and `Card` (a titled group with badges, holding other blocks). A `Page` has a title, subtitle, label/value facts, page-wide notices and `Section`s.
- **Tones:** `neutral`, `good`, `info`, `caution`, `warning`. Each has light and dark theme colours.
- **Self-contained:** styling and the small filter script are inline, and nothing is fetched from the web. Pages work offline, and file names shown on them never leave the machine.
- **Never overwrites:** `write_page` writes a new timestamped file, adding a number if the name is taken.
- **Filter box:** each page has a box that hides sections not containing the typed text. Turn it off with `Page(searchable=False)`.

## Live apps (`server.py`, `controls.py`)
A bot can also run as a live app: a local page with settings controls that runs the bot from the browser. The bot provides an object with `title`, `subtitle`, `facts`, `controls`, `run_label`, `footer()` and `handle(action, values, data)`, and starts it with `LiveServer(app).serve()`. DupeBot's `ui.py` is the working example.
- **Controls:** `Choice` (toggle buttons), `Slider` and `TextField` (one line of text; botview trims it and caps its length, and the app checks what it means). Each has a `default`, which is always marked, and an optional `selected` starting value. Values sent back by the page are checked against the controls before `handle` sees them.
- **Buttons in tables:** an `Action` cell is a button that sends its action and data to `handle` (for example DupeBot's Show in Finder). Send ids the app issued, not paths, and check them in `handle`. Action buttons are hidden on saved pages.
- **Replies:** `handle` returns a `Reply` with a `Page` to show, a `Confirm` (a question with a button that runs a further action, for anything that costs money), an `error` and/or a `status` line. `footer()` is redrawn after every action, which suits running tallies.
- **One action at a time:** a second click while the bot is working gets a "still working" message.
- **Local only:** the server listens on 127.0.0.1 and requires a random per-session key (in the address for the page, in a header for actions), and refuses other host names. Other websites in the browser cannot drive it.
- **Session:** the app lives until Ctrl+C. State kept on the app object, such as a tally, resets on the next start.
