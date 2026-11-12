"""Shared browser pages for BoringBots.

A bot describes what to show as a Page of generic blocks. botview either writes
it as a self-contained HTML file (write_page), or serves a live app with
settings controls that runs the bot from the browser (serve).
"""

from botview.charts import Chart, Series
from botview.controls import Choice, Control, Option, Slider, TextField
from botview.page import TONES, Action, Badge, Block, Card, Page, Section, Table, Text, render
from botview.publish import open_in_browser, write_page
from botview.server import App, Confirm, LiveServer, Reply, serve
from botview.stages import Stage

__all__ = [
    "TONES",
    "Action",
    "App",
    "Badge",
    "Block",
    "Card",
    "Chart",
    "Choice",
    "Confirm",
    "Control",
    "LiveServer",
    "Option",
    "Page",
    "Reply",
    "Section",
    "Series",
    "Slider",
    "Stage",
    "Table",
    "Text",
    "TextField",
    "open_in_browser",
    "render",
    "serve",
    "write_page",
]
