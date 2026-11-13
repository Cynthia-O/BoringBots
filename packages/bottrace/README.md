# bottrace
Tracing shared by all BoringBots: what each request did, step by step, how long each step took, and what it cost.

```sh
uv run traces                    # recent requests across all bots, with timelines for the newest 5
uv run traces --bot DupeBot      # one bot only
uv run traces --detail 20        # timelines for more requests
uv run traces --no-browser       # write the page without opening it
```

## What a trace holds
A **trace** is one human request: a click in a bot's browser app, or one run of a command, from the moment you ask until the bot answers. It is made of nested **spans**, one per step:

| Kind | Examples | Recorded |
|---|---|---|
| `request` | "send to model", "scan", "command line" | the whole request; its length is the time to answer |
| `stage` | walk folder, cluster names, analyze, prioritize | each stage's time and outcome counts |
| `llm` | each model call | model, effort, prompt fingerprint, input/output/thinking tokens, cache tokens, estimated cost, stop reason, request id, and timestamps for the first event (time to first token), first thinking and first visible text |
| `turn` | an agent's turn 1, 2, ... | the model call and the tool calls made in that turn |
| `tool` | `compare_files`, `list_folder` | what was checked and the result |
| `wait` | a "Send? [y/N]" prompt | time spent waiting for you, kept apart from the bot's own latency |

Point-in-time **events** mark milestones, such as when the first result reached the screen.

The traces page shows, for each request: a waterfall timeline; **where the time went** (model calls, tool calls, waiting for you, and the bot's own work, attributed by self time so the parts add up to the total); milestones and time to first token; a table of agent turns with each turn's actions, tokens and cost; and any errors.

## Using it in a bot

```python
from bottrace import Tracer, current_span, span, traced

tracer = Tracer("MyBot")
with tracer.trace("send", trigger="browser app"):  # one human request
    with span("analyze", kind="stage"):  # any code below can add steps
        with span("model call", kind="llm") as s:
            s.set(input_tokens=1200, output_tokens=300)
            s.event("first_event")


@traced("walk folder", kind="stage")  # or trace a whole function
def scan(root): ...
```

Outside an active trace, `span` and `@traced` do nothing, so library code can mark its steps whether or not anyone is tracing.

## Where traces live
A local, append-only SQLite file: `~/.boringbots/traces.sqlite` (override with `BORINGBOTS_TRACES`). Nothing is sent anywhere; no collector or account is needed. Spans are written as each one ends, so a crash keeps every step that finished. The shapes follow OpenTelemetry's (trace, span, parent, attributes, events), so traces could be exported to a tracing service later. File paths appear in span attributes; file contents never do.
