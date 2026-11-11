import pytest

from botview import Badge, Card, Page, Section, Table, Text, render, write_page


def sample_page() -> Page:
    return Page(
        title="Results",
        subtitle="A test",
        facts=[("Model", "m")],
        notices=[Text("2 files missing", tone="warning")],
        sections=[
            Section(
                "pdf-0001",
                badges=[Badge("high", "good")],
                blocks=[
                    Card(
                        "Version series",
                        blocks=[
                            Text("check first", tone="caution", label="Check"),
                            Table(
                                ["File", "Size"],
                                [["a.pdf", 10], ["b.pdf", None]],
                                row_tones=["caution", None],
                                numeric=frozenset({1}),
                            ),
                        ],
                    )
                ],
            )
        ],
    )


def test_render_contains_everything():
    html = render(sample_page())
    for expected in (
        "Results",
        "A test",
        "Model",
        "2 files missing",
        "pdf-0001",
        "high",
        "Version series",
        "check first",
        "a.pdf",
        "row-caution",
        'class="num"',
    ):
        assert expected in html


def test_fine_print_is_small_text():
    html = render(Page("T", notices=[Text("prices may change", fine_print=True)]))
    assert '<p class="fine-print">prices may change</p>' in html


def test_render_escapes_text():
    html = render(Page("T", sections=[Section("<script>alert(1)</script>")]))
    assert "<script>alert(1)" not in html
    assert "&lt;script&gt;" in html


def test_render_loads_nothing_from_the_web():
    html = render(sample_page())
    assert "http://" not in html and "https://" not in html


def test_unknown_tone_is_rejected():
    with pytest.raises(ValueError):
        render(Page("T", notices=[Text("x", tone="purple")]))


def test_row_tones_must_match_rows():
    with pytest.raises(ValueError):
        render(Page("T", sections=[Section("s", blocks=[Table(["a"], [[1]], ["good", "good"])])]))


def test_write_page_never_overwrites(tmp_path):
    first = write_page(sample_page(), tmp_path, name="run")
    first_text = first.read_text()
    second = write_page(sample_page(), tmp_path, name="run")
    assert first != second
    assert first.read_text() == first_text
    assert second.suffix == ".html" and "run" in second.name


def test_action_cells_render_as_buttons_shown_only_in_live_apps():
    from botview import Action
    from botview.page import document, render_body

    page = Page(
        "T",
        sections=[Section("s", blocks=[Table(["", "x"], [[Action("Go", "go", {"id": "1"}), 1]])])],
    )
    html = render(page)
    assert 'class="cell-action" data-action="go"' in html and "&quot;id&quot;" in html
    assert '<body class="">' in html and "body.live .cell-action" in html
    assert '<body class="live">' in document("T", render_body(page), body_class="live")
