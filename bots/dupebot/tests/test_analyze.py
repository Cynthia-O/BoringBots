from pathlib import Path
from types import SimpleNamespace

import anthropic
import pytest

from dupebot.analyze import (
    OMITTED_FILE_FIELDS,
    OMITTED_FROM_INPUT,
    AnalysisError,
    analysis_input,
    analyze,
    batch_clusters,
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


class FakeClient:
    def __init__(self, stop_reason: str = "end_turn"):
        self.requests: list[dict] = []
        self.stop_reason = stop_reason
        self.messages = SimpleNamespace(stream=self._stream)

    def _stream(self, **request):
        self.requests.append(request)
        return FakeStream(
            SimpleNamespace(
                stop_reason=self.stop_reason,
                content=[SimpleNamespace(type="text", text="analysis")],
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
    assert result.text == "analysis" and result.input_tokens == 100


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

    with CallLog(tmp_path / "log.sqlite") as log:
        analyze(full_report(), model="claude-haiku-5-5", client=FakeClient(), log=log)
        [row] = log.rows()
    assert row["model"] == "claude-haiku-5-5" and row["purpose"] == "analyze"
    assert row["input_chars"] > len(system_prompt())
    assert row["output_chars"] == len("analysis")
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
    assert client.requests[0]["output_config"] == {"effort": "low"}
    assert row["effort"] == "low"


def test_model_default_effort_is_left_alone(tmp_path):
    from dupebot.calllog import CallLog

    client = FakeClient()
    with CallLog(tmp_path / "log.sqlite") as log:
        analyze(full_report(), client=client, log=log)
        [row] = log.rows()
    assert "output_config" not in client.requests[0]
    assert row["effort"] is None


def test_unknown_effort_is_rejected_before_any_call():
    client = FakeClient()
    with pytest.raises(ValueError):
        analyze(full_report(), client=client, effort="extreme")
    assert client.requests == []
