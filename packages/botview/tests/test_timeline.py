import re
from datetime import date

import pytest

from botview import Page, Section, Span, Timeline, render
from botview.timeline import _ticks, timeline_html

SPANS = [
    Span("Taxes/", date(2010, 1, 1), date(2015, 1, 1)),
    Span("Bank/", date(2015, 1, 1), date(2020, 1, 1), detail="12 files"),
]


def bars(html_text):
    return [
        (float(left), float(width))
        for left, width in re.findall(r"left: ([\d.]+)%; width: ([\d.]+)%", html_text)
    ]


def test_overall_bar_spans_the_range_with_padding_each_side():
    text = timeline_html(Timeline("Folders", SPANS, overall_label="Whole folder"))
    overall, taxes, bank = bars(text)
    # 10 years with 5% (about half a year) of padding on each side
    assert abs(overall[0] - 100 / 22) < 0.1
    assert abs(overall[0] + overall[1] - 100 * 21 / 22) < 0.1
    assert taxes[0] == overall[0]
    assert abs(bank[0] + bank[1] - (overall[0] + overall[1])) < 0.05
    assert "Whole folder" in text and "2010-01-01 – 2020-01-01" in text


def test_every_row_shows_its_dates_as_text():
    text = timeline_html(Timeline("Folders", SPANS))
    assert "2015-01-01 – 2020-01-01" in text
    assert "Bank/: 2015-01-01 – 2020-01-01 (12 files)" in text


def test_single_day_spans_stay_visible():
    text = timeline_html(Timeline("T", [Span("a", date(2020, 1, 1), date(2020, 1, 1))]))
    assert all(width >= 0.6 for _, width in bars(text))
    assert "2020-01-01</div>" in text


def test_ticks_are_years_for_long_ranges_and_months_for_short():
    years = _ticks(date(2009, 6, 1), date(2020, 6, 1))
    assert [label for _, label in years] == ["2010", "2012", "2014", "2016", "2018", "2020"]
    months = _ticks(date(2023, 11, 15), date(2024, 4, 15))
    assert [label for _, label in months] == ["Dec 2023", "Jan 2024", "Feb", "Mar", "Apr"]


def test_renders_as_a_page_block_and_escapes_labels():
    span = Span("<b>x</b>", date(2020, 1, 1), date(2021, 1, 1))
    text = render(Page("P", sections=[Section("S", blocks=[Timeline("T", [span])])]))
    assert 'class="timeline"' in text
    assert "&lt;b&gt;x&lt;/b&gt;" in text and "<b>x</b>" not in text


def test_empty_timeline_has_no_rows():
    assert "tl-row" not in timeline_html(Timeline("T", []))


def breakdown_timeline(n_spans=3, page_size=None):
    spans = [
        Span(f"f{i}", date(2020, 1, 1), date(2021, 1, 1), breakdown=[i + 1, 1, 0])
        for i in range(n_spans)
    ]
    return Timeline(
        "T",
        spans,
        categories=[".pdf", ".docx", "Other"],
        overall_breakdown=[60, 30, 10],
        breakdown_label="All files",
        breakdown_unit="files",
        page_size=page_size,
        item_name="folders",
    )


def test_breakdown_bars_share_colours_and_the_key_shows_overall_shares():
    text = timeline_html(breakdown_timeline())
    assert text.index("All files") > text.index("f2")  # the overall breakdown comes last
    assert "background: var(--series-1)" in text and "background: var(--series-2)" in text
    assert "var(--series-3)" not in text  # "Other" is grey, not a colour slot
    assert "var(--chart-muted)" in text
    key = text[text.index("tl-key") :]
    assert ".pdf <strong>60%</strong>" in key and "Other <strong>10%</strong>" in key
    assert 'aria-label=".pdf 50%, .docx 50%"' in text  # f0: 1 and 1; zero shares left out
    assert "f1: .pdf, 2 files (67%)" in text


def test_more_than_six_coloured_categories_is_refused():
    with pytest.raises(ValueError, match="at most 6"):
        Timeline("T", [], categories=[f".x{i}" for i in range(7)])
    Timeline("T", [], categories=[*(f".x{i}" for i in range(6)), "Other"])  # fine


def test_pages_hide_later_rows_and_keep_the_axis_and_key_whole():
    text = timeline_html(breakdown_timeline(n_spans=23, page_size=10))
    assert 'data-pages="3"' in text
    assert text.count("hidden") == 13  # rows 11-23 start hidden
    assert 'data-page="3"' in text
    assert "Folders 1–10 of 23" in text
    assert 'class="tl-prev" disabled' in text
    # one axis and one key for every page
    assert text.count("tl-axis") == 1 and text.count("tl-key") == 1


def test_no_pager_when_everything_fits():
    text = timeline_html(breakdown_timeline(n_spans=10, page_size=10))
    assert "tl-pager" not in text and " hidden" not in text


def test_paging_script_is_on_every_page():
    text = render(Page("P"))
    assert ".tl-pager button" in text
