import pytest

from dupebot.settings import Settings, ask_settings, parse_threshold


def answers(*replies):
    it = iter(replies)

    def ask():
        try:
            return next(it)
        except StopIteration:
            raise AssertionError("asked more questions than expected") from None

    return ask


DEFAULTS = Settings(threshold=0.6, model="claude-haiku-5-5", effort=None)


def test_enter_keeps_all_defaults():
    assert ask_settings(analyzing=True, ask=answers(""), interactive=True) == DEFAULTS


def test_yes_keeps_all_defaults():
    assert ask_settings(analyzing=True, ask=answers("y"), interactive=True) == DEFAULTS


def test_no_then_enter_through_each_keeps_defaults():
    got = ask_settings(analyzing=True, ask=answers("n", "", "", ""), interactive=True)
    assert got == DEFAULTS


def test_no_then_change_everything():
    # threshold 0.75, model 2 (Sonnet), effort 2 (low; option 1 is the model default)
    got = ask_settings(analyzing=True, ask=answers("n", "0.75", "2", "2"), interactive=True)
    assert got == Settings(0.75, "claude-sonnet-5-5", "low")


def test_bad_answers_are_asked_again():
    replies = answers("maybe", "n", "high", "0.7", "9", "1", "x", "3")
    got = ask_settings(analyzing=True, ask=replies, interactive=True)
    assert got == Settings(0.7, "claude-haiku-5-5", "medium")


def test_without_analyze_only_the_threshold_is_offered():
    got = ask_settings(analyzing=False, ask=answers("n", "0.8"), interactive=True)
    assert got.threshold == 0.8


def test_flags_are_never_asked_about():
    got = ask_settings(threshold=0.7, analyzing=True, ask=answers("n", "3", ""), interactive=True)
    assert got == Settings(0.7, "claude-opus-5-5", None)


def test_no_questions_when_flags_cover_everything():
    got = ask_settings(
        0.7, "claude-opus-5-5", "high", analyzing=True, ask=answers(), interactive=True
    )
    assert got == Settings(0.7, "claude-opus-5-5", "high")


def test_effort_flag_default_means_model_default():
    got = ask_settings(
        0.7, "claude-opus-5-5", "default", analyzing=True, ask=answers(), interactive=True
    )
    assert got.effort is None


def test_not_interactive_uses_defaults_without_asking():
    assert ask_settings(analyzing=True, ask=answers(), interactive=False) == DEFAULTS


def test_end_of_input_takes_defaults():
    def eof():
        raise EOFError

    assert ask_settings(analyzing=True, ask=eof, interactive=True) == DEFAULTS


def test_parse_threshold_rejects_out_of_range():
    with pytest.raises(ValueError):
        parse_threshold("1.5")
    with pytest.raises(ValueError):
        parse_threshold("y")
