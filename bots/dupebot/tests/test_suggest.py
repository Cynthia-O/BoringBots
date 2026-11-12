import json
from types import SimpleNamespace

import pytest

from dupebot.analyze import BatchResult
from dupebot.calllog import CallLog
from dupebot.cluster import cluster_records, scan
from dupebot.report import build_report
from dupebot.suggest import (
    TOOLS,
    FileTools,
    SuggestionError,
    check_suggestions,
    suggest,
    suggestion_cap,
)

# --- a scanned folder with an exact copy and an earlier version ---


@pytest.fixture
def report(tmp_path):
    root = tmp_path / "docs"
    (root / "old").mkdir(parents=True)
    (root / "Budget Review_v2.docx").write_bytes(b"a" * 300_000)
    (root / "Budget Review_v2 (1).docx").write_bytes(b"a" * 300_000)  # identical copy
    (root / "old" / "Budget Review_draft.docx").write_bytes(b"b" * 290_000)
    (root / "secret.txt").write_text("not clustered")
    s = scan(root)
    return build_report(root, s, cluster_records(s.records, 0.6), 0.6)


def analysed(report) -> list[BatchResult]:
    [cluster] = report["clusters"]
    roles = {
        "Budget Review_v2.docx": "original",
        "Budget Review_v2 (1).docx": "copy",
        "old/Budget Review_draft.docx": "earlier_version",
    }
    group = {
        "files": [{"path": p, "role": r} for p, r in roles.items()],
        "reason": "r",
        "relationship": "copies",
        "check_before_deleting": "",
    }
    answer = [{"cluster_id": cluster["cluster_id"], "groups": [group]}]
    return [BatchResult((cluster["cluster_id"],), answer, [], 10, 10)]


# --- a scripted model ---


def usage(i=100, o=50, thinking=20):
    return SimpleNamespace(
        input_tokens=i,
        output_tokens=o,
        output_tokens_details=SimpleNamespace(thinking_tokens=thinking),
    )


def tool_turn(*calls):
    blocks = [
        SimpleNamespace(type="tool_use", id=f"t{i}", name=name, input=args)
        for i, (name, args) in enumerate(calls)
    ]
    return SimpleNamespace(stop_reason="tool_use", content=blocks, usage=usage())


def final_turn(suggestions, summary="done"):
    text = json.dumps({"suggestions": suggestions, "summary": summary})
    return SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text=text)], usage=usage()
    )


class Stream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message


class Scripted:
    def __init__(self, *turns):
        self.turns = list(turns)
        self.requests = []
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        # Copy the conversation as it was at this call; the agent appends to it later.
        self.requests.append({**request, "messages": list(request["messages"])})
        return Stream(self.turns.pop(0))


COPY, ORIGINAL, DRAFT = (
    "Budget Review_v2 (1).docx",
    "Budget Review_v2.docx",
    "old/Budget Review_draft.docx",
)


def suggestion(delete, keep, cid=None):
    return {
        "cluster_id": cid,
        "delete": delete,
        "keep": keep,
        "reason": "why",
    }


# --- the cap ---


@pytest.mark.parametrize(
    "clusters, cap", [(0, 0), (1, 1), (3, 1), (4, 1), (5, 2), (40, 10), (100, 25), (108, 25)]
)
def test_cap_is_25_or_a_quarter_of_clusters_whichever_is_smaller(clusters, cap):
    assert suggestion_cap(clusters) == cap


def test_cap_counts_clusters_not_flagged_files(report):
    # 8 analysed clusters, only 2 flagged files: a quarter of the clusters is 2,
    # where a quarter of the flagged files would have allowed just 1.
    results = analysed(report)
    extra = [{"cluster_id": f"pdf-{i:04d}", "groups": []} for i in range(2, 9)]
    results[0].clusters.extend(extra)
    client = Scripted(final_turn([]))
    result = suggest(report, results, client=client)
    assert result.cap == 2 and result.candidates == 2
    assert json.loads(client.requests[0]["messages"][0]["content"])["max_suggestions"] == 2


# --- the agent loop ---


def test_agent_checks_with_tools_then_suggests(report):
    cid = report["clusters"][0]["cluster_id"]
    client = Scripted(
        tool_turn(("compare_files", {"path_a": COPY, "path_b": ORIGINAL})),
        final_turn([suggestion(COPY, ORIGINAL, cid)]),
    )
    result = suggest(report, analysed(report), client=client)
    assert result.cap == 1 and result.candidates == 2
    [s] = result.suggestions
    assert (s.delete, s.keep, s.identical) == (COPY, ORIGINAL, True)
    assert [c.outcome for c in result.checks] == ["identical"]
    assert result.calls == 2 and result.thinking_tokens == 40

    first, second = client.requests
    assert first["tools"] == TOOLS and first["tool_choice"] == {"type": "auto"}
    assert json.loads(first["messages"][0]["content"])["max_suggestions"] == 1
    tool_result = second["messages"][-1]["content"][0]
    assert tool_result["tool_use_id"] == "t0"
    assert json.loads(tool_result["content"])["identical"] is True


def test_identical_comes_from_our_checks_not_the_model(report):
    cid = report["clusters"][0]["cluster_id"]
    # The model never compared, so DupeBot does not claim the files are identical.
    result = suggest(
        report, analysed(report), client=Scripted(final_turn([suggestion(COPY, ORIGINAL, cid)]))
    )
    assert result.suggestions[0].identical is None


def test_last_turn_turns_tools_off_so_the_agent_must_answer(report, monkeypatch):
    monkeypatch.setattr("dupebot.suggest.MAX_TURNS", 2)
    client = Scripted(tool_turn(("list_folder", {"folder": ""})), final_turn([]))
    result = suggest(report, analysed(report), client=client)
    assert [r["tool_choice"]["type"] for r in client.requests] == ["auto", "none"]
    assert result.suggestions == []


def test_unusable_final_answers_are_errors(report):
    bad = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="nope")], usage=usage()
    )
    with pytest.raises(SuggestionError):
        suggest(report, analysed(report), client=Scripted(bad))


def test_no_candidates_means_no_calls(report):
    results = analysed(report)
    for g in results[0].clusters[0]["groups"]:
        for f in g["files"]:
            f["role"] = "member"
    client = Scripted()
    result = suggest(report, results, client=client)
    assert client.requests == [] and result.candidates == 0 and result.suggestions == []


def test_agent_calls_are_logged_under_the_run(report, tmp_path):
    client = Scripted(tool_turn(("list_folder", {"folder": "old"})), final_turn([]))
    with CallLog(tmp_path / "log.sqlite") as log:
        suggest(report, analysed(report), client=client, log=log, run_id="run1")
        rows = log.rows("run1")
    assert [r["purpose"] for r in rows] == ["suggest", "suggest"]
    assert rows[0]["stop_reason"] == "tool_use" and rows[0]["thinking_tokens"] == 20


# --- the tools stay inside the scan and only read ---


def test_list_folder_lists_inside_the_scanned_folder(report):
    tools = FileTools(report)
    text, is_error = tools.run("list_folder", {"folder": ""})
    names = {e["name"] for e in json.loads(text)["entries"]}
    assert not is_error and {"old", "secret.txt", ORIGINAL} <= names


@pytest.mark.parametrize("folder", ["..", "../..", "/etc", "old/../.."])
def test_list_folder_refuses_to_leave_the_scanned_folder(report, folder):
    text, is_error = FileTools(report).run("list_folder", {"folder": folder})
    assert is_error and "error" in json.loads(text)


def test_compare_only_accepts_clustered_files(report):
    tools = FileTools(report)
    for a, b in [("/etc/passwd", ORIGINAL), ("secret.txt", ORIGINAL), (ORIGINAL, ORIGINAL)]:
        _, is_error = tools.run("compare_files", {"path_a": a, "path_b": b})
        assert is_error
    text, is_error = tools.run("compare_files", {"path_a": DRAFT, "path_b": ORIGINAL})
    assert not is_error and json.loads(text)["identical"] is False


def test_bad_tool_input_is_reported_to_the_agent(report):
    tools = FileTools(report)
    assert tools.run("delete_file", {"path": ORIGINAL})[1] is True
    assert tools.run("compare_files", {"path_a": 3})[1] is True
    assert all(c.is_error for c in tools.checks)


def test_tools_never_change_the_files(report):
    root = report["root"]
    from pathlib import Path

    before = {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()}
    tools = FileTools(report)
    tools.run("compare_files", {"path_a": COPY, "path_b": ORIGINAL})
    tools.run("list_folder", {"folder": "old"})
    assert {p: p.read_bytes() for p in Path(root).rglob("*") if p.is_file()} == before


# --- every suggestion is checked against the scan ---


def test_bad_suggestions_are_dropped_with_a_reason(report):
    cid = report["clusters"][0]["cluster_id"]
    tools = FileTools(report)
    raw = [
        suggestion(COPY, ORIGINAL, cid),
        suggestion(ORIGINAL, COPY, cid),  # would delete both copies
        suggestion(DRAFT, DRAFT, cid),  # same file
        suggestion("secret.txt", ORIGINAL, cid),  # not in the cluster
        suggestion(COPY, DRAFT, "pdf-9999"),  # no such cluster
    ]
    kept, warnings = check_suggestions(raw, report, cap=5, tools=tools)
    assert [(s.delete, s.keep) for s in kept] == [(COPY, ORIGINAL)]
    assert len(warnings) == 4 and any("both copies" in w for w in warnings)


def test_suggestions_over_the_cap_are_dropped(report):
    cid = report["clusters"][0]["cluster_id"]
    raw = [suggestion(COPY, ORIGINAL, cid), suggestion(DRAFT, ORIGINAL, cid)]
    kept, warnings = check_suggestions(raw, report, cap=1, tools=FileTools(report))
    assert len(kept) == 1 and "limit of 1" in warnings[0]
