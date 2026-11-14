"""Tracing shared by all BoringBots: what each request did, step by step.

    tracer = Tracer("MyBot")
    with tracer.trace("send", trigger="browser"):   # one human request
        with span("analyze", kind="stage"):         # any code below can add steps
            with span("model call", kind="llm") as s:
                s.set(input_tokens=1200, output_tokens=300)

Traces go to a local, append-only SQLite file (~/.boringbots/traces.sqlite, or
BORINGBOTS_TRACES). `uv run traces` shows them.
"""

from bottrace.summary import TraceSummary, summarize, waterfall_rows
from bottrace.trace import (
    Span,
    Tracer,
    current_span,
    current_trace_id,
    default_path,
    finished_spans,
    read_traces,
    span,
    traced,
)
from bottrace.view import timing_blocks, traces_facts, traces_page, traces_sections

__all__ = [
    "Span",
    "TraceSummary",
    "Tracer",
    "current_span",
    "current_trace_id",
    "default_path",
    "finished_spans",
    "read_traces",
    "span",
    "summarize",
    "timing_blocks",
    "traced",
    "traces_facts",
    "traces_page",
    "traces_sections",
    "waterfall_rows",
]
