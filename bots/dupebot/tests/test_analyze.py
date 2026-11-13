import json
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

from dupebot.analyze import (
    OMITTED_FILE_FIELDS,
    OMITTED_FROM_INPUT,
    OUTPUT_SCHEMA,
    AnalysisError,
    analysis_input,
    analyze,
    batch_clusters,
    check_answer,
    system_prompt,
)
from dupebot.cleaning import clean_name
from dupebot.cli import run_analysis
from dupebot.cluster import Cluster, FileRecord, ScanResult
from dupebot.report import build_report


def full_report() -> dict:
    record = FileRecord(Path("FINAL_a_2023.pdf"), 1, 0.0, clean_name("FINAL_a_2023.pdf"))
    return build_report(Path("."), ScanResult(), [Cluster(".pdf", (record,))], 0.6)


# --- what the model receives ---


def test_omitted_fields_are_not_sent_to_the_model():
    data = analysis_input(full_report())
    assert not OMITTED_FROM_INPUT & set(data)
    for cluster in data["clusters"]:
        for f in cluster["files"]:
            assert not OMITTED_FILE_FIELDS & set(f)


def test_paths_are_sent_relative_to_the_scanned_folder():
    root = Path("/Users/someone/Documents")
    path = root / "taxes" / "2023" / "W2.pdf"
    record = FileRecord(path, 1, 0.0, clean_name(path.name))
    report = build_report(root, ScanResult(), [Cluster(".pdf", (record,))], 0.6)
    data = analysis_input(report)
    assert data["clusters"][0]["files"][0]["path"] == "taxes/2023/W2.pdf"
    assert "someone" not in str(data)
    assert report["clusters"][0]["files"][0]["path"] == str(path)  # report unchanged


def test_analysis_input_leaves_the_report_intact():
    report = full_report()
    analysis_input(report)
    assert OMITTED_FROM_INPUT <= set(report)
    assert OMITTED_FILE_FIELDS <= set(report["clusters"][0]["files"][0])


def test_prompt_describes_every_input_field():
    """Keeps the prompt in step with what the model receives."""
    data = analysis_input(full_report())
    fields = set(data) | set(data["clusters"][0]) | set(data["clusters"][0]["files"][0])
    prompt = system_prompt()
    missing = sorted(f for f in fields if f"`{f}`" not in prompt)
    assert not missing, f"prompt does not describe: {missing}"


def test_prompt_does_not_mention_omitted_fields():
    prompt = system_prompt()
    mentioned = sorted(f for f in OMITTED_FROM_INPUT | OMITTED_FILE_FIELDS if f"`{f}`" in prompt)
    assert not mentioned, f"prompt mentions fields the model never sees: {mentioned}"


# --- batching ---


def cluster(cluster_id: str, n: int) -> dict:
    return {"cluster_id": cluster_id, "extension": ".pdf", "files": [{}] * n}


def test_batch_clusters_packs_whole_clusters():
    batches = batch_clusters([cluster("a", 3), cluster("b", 3), cluster("c", 5)], max_files=6)
    assert [[c["cluster_id"] for c in b] for b in batches] == [["a", "b"], ["c"]]


def test_batch_clusters_never_splits_a_large_cluster():
    batches = batch_clusters([cluster("a", 2), cluster("big", 9), cluster("c", 1)], max_files=6)
    assert [[c["cluster_id"] for c in b] for b in batches] == [["a"], ["big"], ["c"]]


# --- the model call (no network: a stand-in client records each request) ---


class FakeStream:
    def __init__(self, message):
        self.message = message

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_final_message(self):
        return self.message

    def __iter__(self):
        """Events as the API streams them: a start, then the text in small pieces."""
        yield SimpleNamespace(type="message_start")
        text = "".join(b.text for b in self.message.content if b.type == "text")
        for i in range(0, len(text), 7):
            yield SimpleNamespace(type="text", text=text[i : i + 7])


def answer_for(payload: str) -> str:
    """A schema-shaped answer that puts each cluster's files in one unrelated group."""
    clusters = json.loads(payload)["clusters"]
    return json.dumps(
        {
            "clusters": [
                {
                    "cluster_id": c["cluster_id"],
                    "groups": [
                        {
                            "files": [{"path": f["path"], "role": "member"} for f in c["files"]],
                            "reason": "names differ",
                            "relationship": "unrelated",
                            "check_before_deleting": "",
                        }
                    ],
                }
                for c in clusters
            ]
        }
    )


class FakeClient:
    def __init__(self, stop_reason: str = "end_turn", text: str | None = None):
        self.requests: list[dict] = []
        self.stop_reason = stop_reason
        self.text = text  # None: a valid answer for whatever was sent
        self.last_text = ""
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        self.requests.append(request)
        self.last_text = self.text or answer_for(request["messages"][0]["content"])
        return FakeStream(
            SimpleNamespace(
                stop_reason=self.stop_reason,
                content=[SimpleNamespace(type="text", text=self.last_text)],
                usage=SimpleNamespace(input_tokens=100, output_tokens=20),
            )
        )


def test_analyze_sends_prompt_and_trimmed_input():
    client = FakeClient()
    [result] = analyze(full_report(), model="claude-haiku-5-5", client=client)
    [request] = client.requests
    assert request["model"] == "claude-haiku-5-5"
    assert request["system"] == system_prompt()
    sent = request["messages"][0]["content"]
    assert "cleaned_name" not in sent and "settings" not in sent
    assert result.input_tokens == 100
    [cluster] = result.clusters
    assert cluster["groups"][0]["files"] == [{"path": "FINAL_a_2023.pdf", "role": "member"}]
    assert result.warnings == []


def test_output_format_is_enforced_through_the_api():
    client = FakeClient()
    analyze(full_report(), client=client)
    fmt = client.requests[0]["output_config"]["format"]
    assert fmt == {"type": "json_schema", "schema": OUTPUT_SCHEMA}


def test_unparseable_answer_is_an_error():
    with pytest.raises(AnalysisError):
        analyze(full_report(), client=FakeClient(text="not json"))


def test_check_answer_finds_missing_extra_and_duplicate_files():
    batch = [
        {"cluster_id": "pdf-0001", "files": [{"path": "a"}, {"path": "b"}]},
        {"cluster_id": "pdf-0002", "files": [{"path": "c"}]},
    ]

    def group(*paths):
        return {"files": [{"path": p, "role": "member"} for p in paths]}

    answer = [
        {"cluster_id": "pdf-0001", "groups": [group("a", "x"), group("a")]},
        {"cluster_id": "pdf-0009", "groups": []},
    ]
    assert check_answer(batch, answer) == [
        "pdf-0001: b was left out",
        "pdf-0001: x is not in this cluster",
        "pdf-0001: a is in more than one group",
        "pdf-0009: not a cluster in the input",
        "pdf-0002: not analysed",
    ]


@pytest.mark.parametrize("stop_reason", ["refusal", "max_tokens"])
def test_analyze_raises_when_the_answer_is_unusable(stop_reason):
    with pytest.raises(AnalysisError):
        analyze(full_report(), client=FakeClient(stop_reason))


def test_run_analysis_does_not_send_without_confirmation(monkeypatch):
    def fail(*args, **kwargs):
        raise AssertionError("must not call the model")

    monkeypatch.setattr("dupebot.cli.analyze", fail)
    assert run_analysis(full_report(), "m", ask=lambda: "n", interactive=True) == 1
    assert run_analysis(full_report(), "m", interactive=False) == 1


def test_run_analysis_explains_a_missing_key(monkeypatch, capsys):
    def no_key(*args, **kwargs):
        raise anthropic.CredentialsError("no credentials")

    monkeypatch.setattr("dupebot.cli.analyze", no_key)
    assert run_analysis(full_report(), "m", confirmed=True) == 1
    assert ".env.example" in capsys.readouterr().err


# --- call logging ---


class FailingClient:
    def __init__(self):
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        raise RuntimeError("network down")


def test_successful_calls_are_logged(tmp_path):
    from dupebot.calllog import CallLog

    client = FakeClient()
    with CallLog(tmp_path / "log.sqlite") as log:
        analyze(full_report(), model="claude-haiku-5-5", client=client, log=log)
        [row] = log.rows()
    assert row["model"] == "claude-haiku-5-5" and row["purpose"] == "analyze"
    assert row["input_chars"] > len(system_prompt())
    assert row["output_chars"] == len(client.last_text)
    assert (row["input_tokens"], row["output_tokens"]) == (100, 20)
    assert row["stop_reason"] == "end_turn" and row["error"] is None


def test_unusable_answers_are_still_logged(tmp_path):
    from dupebot.calllog import CallLog

    with CallLog(tmp_path / "log.sqlite") as log:
        with pytest.raises(AnalysisError):
            analyze(full_report(), client=FakeClient("max_tokens"), log=log)
        [row] = log.rows()
    assert row["stop_reason"] == "max_tokens" and row["output_tokens"] == 20


def test_failed_calls_are_logged(tmp_path):
    from dupebot.calllog import CallLog

    with CallLog(tmp_path / "log.sqlite") as log:
        with pytest.raises(RuntimeError):
            analyze(full_report(), client=FailingClient(), log=log)
        [row] = log.rows()
    assert row["error"] == "RuntimeError: network down"
    assert row["input_tokens"] is None


# --- thinking effort ---


def test_effort_is_sent_and_logged_when_set(tmp_path):
    from dupebot.calllog import CallLog

    client = FakeClient()
    with CallLog(tmp_path / "log.sqlite") as log:
        analyze(full_report(), client=client, log=log, effort="low")
        [row] = log.rows()
    assert client.requests[0]["output_config"]["effort"] == "low"
    assert row["effort"] == "low"


def test_model_default_effort_is_left_alone(tmp_path):
    from dupebot.calllog import CallLog

    client = FakeClient()
    with CallLog(tmp_path / "log.sqlite") as log:
        analyze(full_report(), client=client, log=log)
        [row] = log.rows()
    assert "effort" not in client.requests[0]["output_config"]
    assert row["effort"] is None


def test_unknown_effort_is_rejected_before_any_call():
    client = FakeClient()
    with pytest.raises(ValueError):
        analyze(full_report(), client=client, effort="extreme")
    assert client.requests == []


# --- results page ---


def test_run_analysis_writes_a_results_page(monkeypatch, capsys, tmp_path):
    report = full_report()

    def fake_analyze(report, model, log, effort, run_id=None):
        return analyze(
            report, model=model, client=FakeClient(), log=log, effort=effort, run_id=run_id
        )

    opened = []
    monkeypatch.setattr("dupebot.cli.analyze", fake_analyze)
    monkeypatch.setattr("dupebot.cli.open_in_browser", lambda path: opened.append(path) or True)
    assert run_analysis(report, "claude-haiku-5-5", confirmed=True) == 0
    [page] = opened
    assert page.parent == tmp_path / "pages"  # next to the (test) call log
    html = page.read_text()
    assert "pdf-0001" in html and "FINAL_a_2023.pdf" in html and "Unrelated" in html
    assert "pdf-0001: unrelated (1 files)" in capsys.readouterr().out


def test_no_browser_still_writes_the_page(monkeypatch, tmp_path):
    def fake_analyze(report, model, log, effort, run_id=None):
        return analyze(
            report, model=model, client=FakeClient(), log=log, effort=effort, run_id=run_id
        )

    def fail(path):
        raise AssertionError("must not open a browser")

    monkeypatch.setattr("dupebot.cli.analyze", fake_analyze)
    monkeypatch.setattr("dupebot.cli.open_in_browser", fail)
    assert run_analysis(full_report(), "m", confirmed=True, open_browser=False) == 0
    assert len(list((tmp_path / "pages").glob("*.html"))) == 1


# --- streaming answers cluster by cluster ---


def test_cluster_stream_hands_over_each_entry_once_when_complete():
    import json as _json

    from dupebot.analyze import ClusterStream

    entries = [
        {"cluster_id": "pdf-0001", "groups": [{"files": [{"path": 'odd "quoted" {name}.pdf'}]}]},
        {"cluster_id": "pdf-0002", "groups": []},
    ]
    text = _json.dumps({"clusters": entries}, indent=1)
    got = []
    stream = ClusterStream(got.append)
    for size in (1, 3, 5):  # any split of the text, even one character at a time
        got.clear()
        stream = ClusterStream(got.append)
        for i in range(0, len(text), size):
            stream.feed(text[i : i + size])
        assert got == entries


def test_cluster_stream_waits_for_an_unfinished_entry():
    from dupebot.analyze import ClusterStream

    got = []
    stream = ClusterStream(got.append)
    stream.feed('{"clusters": [{"cluster_id": "a", "groups": []}, {"cluster_id": "b", "gro')
    assert [e["cluster_id"] for e in got] == ["a"]
