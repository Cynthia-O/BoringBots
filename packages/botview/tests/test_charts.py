import pytest

from botview import Chart, Page, Section, Series, render
from botview.charts import _ticks, chart_html


def test_ticks_are_clean_and_cover_the_maximum():
    assert _ticks(0.37) == [0.0, 0.1, 0.2, 0.3, 0.4]
    assert _ticks(1234) == [0.0, 500.0, 1000.0, 1500.0]
    assert _ticks(0) == [0.0, 1.0]


def test_single_series_has_no_legend_and_several_do():
    one = chart_html(Chart("Spend", "columns", ["a", "b"], [Series("x", [1, 2])]))
    three = chart_html(
        Chart("Tokens", "columns", ["a"], [Series("In", [1]), Series("Out", [2]), Series("T", [3])])
    )
    assert 'class="legend"' not in one
    assert three.count('class="legend-item"') == 3


def test_columns_have_rounded_tops_and_a_hit_area_per_x():
    html = chart_html(Chart("Spend", "columns", ["a", "b", "c"], [Series("x", [1, 0, 3])]))
    assert html.count('class="chart-hit"') == 3
    assert html.count("<path") == 2  # a zero day draws no column
    assert "Q" in html  # the rounded data end


def test_line_labels_only_its_end():
    html = chart_html(Chart("Total", "line", ["a", "b"], [Series("x", [1.5, 4.25])], unit="usd"))
    assert html.count('class="end-label"') == 1 and "$4.25" in html


def test_labels_are_escaped():
    html = chart_html(Chart("<b>t</b>", "columns", ["<i>day</i>"], [Series("<s>", [1])]))
    assert "<b>" not in html and "<i>" not in html and "<s>" not in html
    assert "&lt;b&gt;t&lt;/b&gt;" in html and "&lt;i&gt;day&lt;/i&gt;" in html


@pytest.mark.parametrize(
    "kwargs",
    [
        dict(kind="pie", series=[Series("x", [1])]),
        dict(kind="line", series=[Series("x", [1]), Series("y", [1])]),
        dict(kind="columns", series=[Series("x", [1, 2])]),  # wrong length
        dict(kind="columns", series=[Series(str(i), [1]) for i in range(4)]),  # too many
    ],
)
def test_bad_charts_are_rejected(kwargs):
    with pytest.raises(ValueError):
        Chart("t", x_labels=["a"], **kwargs)


def test_charts_render_inside_pages_without_web_requests():
    page = Page(
        "T",
        hero=("Total", "$1.00"),
        sections=[Section("s", blocks=[Chart("c", "line", ["a"], [Series("x", [1])])])],
    )
    html = render(page)
    assert 'class="hero-value">$1.00' in html and "<svg" in html
    assert "http://" not in html and "https://" not in html
