import pytest

from dupebot.analyze import MODEL_CHOICES
from dupebot.pricing import PRICES, call_cost, usd


def test_every_menu_model_has_a_price():
    assert {c.model for c in MODEL_CHOICES} <= set(PRICES)


def test_thinking_and_response_are_billed_at_the_output_rate():
    cost = call_cost("claude-sonnet-5-5", 1_000_000, 300_000, thinking_tokens=200_000)
    assert cost.input == pytest.approx(2.00)
    assert cost.thinking == pytest.approx(2.00)
    assert cost.response == pytest.approx(1.00)
    assert cost.total == pytest.approx(5.00)


def test_haiku_charges_more_for_long_prompts_per_request():
    short = call_cost("claude-haiku-5-5", 100_000, 0)
    long = call_cost("claude-haiku-5-5", 100_001, 1_000_000)
    assert short.input == pytest.approx(0.01)
    assert long.input == pytest.approx(100_001 * 0.50 / 1_000_000)
    assert long.output == pytest.approx(2.50)


def test_unreported_thinking_is_counted_as_response():
    cost = call_cost("claude-opus-5-5", 0, 1_000_000)
    assert cost.response == pytest.approx(20.00) and cost.thinking == 0


def test_unknown_model_has_no_price():
    assert call_cost("some-other-model", 10, 10) is None


def test_usd_shows_small_amounts():
    assert usd(0) == "$0.00"
    assert usd(0.00015) == "$0.0001" or usd(0.00015) == "$0.0002"
    assert usd(0.25) == "$0.250"
    assert usd(12.5) == "$12.50"
