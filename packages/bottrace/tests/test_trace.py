import time

import pytest

from bottrace import (
    Tracer,
    cli,
    current_span,
    current_trace_id,
    finished_spans,
    read_traces,
    span,
    summarize,
    timing_blocks,
    traced,
    traces_page,
    waterfall_rows,
)
from botview import render


@pytest.fixture
def path(tmp_path, monkeypatch):
    p = tmp_path / "traces.sqlite"
    monkeypatch.setenv("BORINGBOTS_TRACES", str(p))
    return p


def run_agent_like_request():
    with Tracer("TestBot").trace("send", trigger="test") as root:
        with span("analyze", kind="stage"):
            with span("analyze call", kind="llm") as c:
                c.event("first_event")
                time.sleep(0.01)
                c.set(input_tokens=1000, output_tokens=300, thinking_tokens=200, cost_usd=0.0003)
        with span("prioritize", kind="stage"):
            for n in (1, 2):
                with span(f"turn {n}", kind="turn"):
                    with span("suggest call", kind="llm") as c:
                        c.set(input_tokens=500, output_tokens=50, stop_reason="tool_use")
                    if n == 1:
                        with span("compare_files", kind="tool", target="a ↔ b") as t:
                            t.set(outcome="identical", is_error=False)
        root.event("first_result_shown")
        return root.trace_id


def test_spans_nest_and_are_written_when_they_end(path):
    trace_id = run_agent_like_request()
    [spans] = read_traces()
    assert {s["trace_id"] for s in spans} == {trace_id}
    by_name = {s["name"]: s for s in spans}
    root = by_name["send"]
    assert root["parent_id"] is None and root["kind"] == "request"
    assert by_name["analyze call"]["parent_id"] == by_name["analyze"]["span_id"]
    assert by_name["compare_files"]["parent_id"] == by_name["turn 1"]["span_id"]
    assert by_name["analyze call"]["attributes"]["input_tokens"] == 1000
    assert all(s["offset_ms"] >= 0 and s["duration_ms"] >= 0 for s in spans)


def test_summary_counts_turns_tokens_and_attributes_all_the_time(path):
    run_agent_like_request()
    [spans] = read_traces()
    s = summarize(spans)
    assert (s.llm_calls, s.turns, s.tool_calls) == (3, 2, 1)
    assert (s.input_tokens, s.output_tokens, s.thinking_tokens) == (2000, 400, 200)
    assert s.cost_usd == pytest.approx(0.0003)
    assert sum(s.time_by_kind.values()) == pytest.approx(s.total_ms, rel=0.01)
    assert [t.actions for t in s.turn_rows] == [["compare_files(a ↔ b)"], []]
    assert [m[0] for m in s.milestones] == ["first_result_shown"]
    assert len(s.first_token_ms) == 1


def test_waterfall_lists_parents_before_children(path):
    run_agent_like_request()
    [spans] = read_traces()
    rows = waterfall_rows(spans)
    names = [r.name if hasattr(r, "name") else r.label for r in rows]
    assert names[0] == "send" and rows[0].depth == 0
    assert names.index("turn 1") < names.index("compare_files") < names.index("turn 2")


def test_spans_outside_a_trace_do_nothing(path):
    with span("loose", kind="stage") as s:
        s.set(x=1)
        s.event("y")
    assert current_trace_id() is None and current_span().trace_id is None
    assert read_traces() == []


def test_errors_are_recorded_and_still_raised(path):
    with pytest.raises(RuntimeError):
        with Tracer("TestBot").trace("send"):
            with span("model call", kind="llm"):
                raise RuntimeError("network down")
    [spans] = read_traces()
    assert all(s["status"] == "error" for s in spans)
    assert "network down" in summarize(spans).errors[0]


def test_traced_decorator_and_running_trace_timing(path):
    @traced("walk folder", kind="stage")
    def walk():
        current_span().set(files=3)
        return "done"

    with Tracer("TestBot").trace("scan"):
        assert walk() == "done"
        spans, elapsed = finished_spans()
        assert [s["name"] for s in spans] == ["walk folder"]
        assert spans[0]["attributes"] == {"files": 3}
        html = render_blocks(timing_blocks(spans, elapsed))
        assert "Timeline" in html and "Where the time went" in html


def test_waits_on_the_person_are_kept_apart(path):
    with Tracer("TestBot").trace("command line"):
        with span("waiting for your go-ahead", kind="wait"):
            time.sleep(0.02)
    s = summarize(read_traces()[0])
    assert s.time_by_kind["wait"] >= 15


def test_bad_kinds_are_rejected(path):
    with pytest.raises(ValueError):
        with span("x", kind="mystery"):
            pass


def test_traces_page_and_command(path, tmp_path, monkeypatch):
    run_agent_like_request()
    html = render(traces_page(read_traces()))
    assert "Recent requests" in html and "TestBot" in html and "compare_files" in html
    assert "http://" not in html and "https://" not in html
    monkeypatch.setattr(cli, "PAGES_DIR", tmp_path / "pages")
    monkeypatch.setattr(cli, "open_in_browser", lambda p: True)
    assert cli.main([]) == 0 and cli.main(["--bot", "nobody", "--no-browser"]) == 0
    assert len(list((tmp_path / "pages").glob("*traces*.html"))) == 2


def test_reading_never_changes_the_trace_file(path):
    run_agent_like_request()
    before = path.read_bytes()
    read_traces()
    assert path.read_bytes() == before


def render_blocks(blocks):
    from botview import Page, Section

    return render(Page("T", sections=[Section("s", blocks=list(blocks))]))
