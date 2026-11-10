from dataclasses import fields

from dupebot.calllog import SCHEMA, CallLog, CallRecord, default_db_path


def test_every_record_field_has_a_column():
    assert {f.name for f in fields(CallRecord)} <= set(SCHEMA.split())


def test_records_are_appended_and_survive_reopening(tmp_path):
    path = tmp_path / "nested" / "calls.sqlite"
    first = CallRecord("run1", "2026-11-09T10:00:00+00:00", "analyze", "m", "abc", 100, 5)
    with CallLog(path) as log:
        log.record(first)
    with CallLog(path) as log:
        log.record(CallRecord("run2", "2026-11-09T11:00:00+00:00", "analyze", "m", "abc", 50, 7))
        rows = log.rows()
    assert [r["run_id"] for r in rows] == ["run1", "run2"]
    assert rows[0]["input_chars"] == 100 and rows[0]["output_tokens"] is None


def test_db_location_can_be_overridden(monkeypatch, tmp_path):
    monkeypatch.setenv("DUPEBOT_DB", str(tmp_path / "x.sqlite"))
    assert default_db_path() == tmp_path / "x.sqlite"
    monkeypatch.delenv("DUPEBOT_DB")
    assert default_db_path().parts[-2:] == (".dupebot", "dupebot.sqlite")
