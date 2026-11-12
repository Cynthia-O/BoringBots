"""Estimated cost of model calls, from token counts and published prices.

Prices are copied by hand from https://platform.claude.com/docs/en/about-claude/pricing
and go stale when Anthropic changes them: update PRICES and PRICES_CHECKED
together. They are standard API prices in USD, without batch, fast-mode,
US-only inference or prompt-caching adjustments (DupeBot uses none of these).
Thinking tokens are billed at the output rate.
"""

from dataclasses import dataclass

PRICES_CHECKED = "2026-11-10"


@dataclass(frozen=True)
class Rate:
    input_per_mtok: float  # USD per million input tokens
    output_per_mtok: float  # USD per million output tokens


@dataclass(frozen=True)
class ModelPrice:
    rate: Rate
    # Some models charge more for a request whose prompt is over a set length.
    # Each request is priced on its own.
    long_prompt_rate: Rate | None = None
    long_prompt_over: int | None = None  # input tokens

    def rate_for(self, input_tokens: int) -> Rate:
        if self.long_prompt_rate and self.long_prompt_over and input_tokens > self.long_prompt_over:
            return self.long_prompt_rate
        return self.rate


PRICES = {
    "claude-haiku-5-5": ModelPrice(Rate(0.10, 0.50), Rate(0.50, 2.50), 100_000),
    "claude-sonnet-5-5": ModelPrice(Rate(2.00, 10.00)),
    "claude-opus-5-5": ModelPrice(Rate(4.00, 20.00)),
}


@dataclass(frozen=True)
class Cost:
    input: float = 0.0
    response: float = 0.0
    thinking: float = 0.0

    @property
    def output(self) -> float:
        return self.response + self.thinking

    @property
    def total(self) -> float:
        return self.input + self.output

    def __add__(self, other: "Cost") -> "Cost":
        return Cost(
            self.input + other.input,
            self.response + other.response,
            self.thinking + other.thinking,
        )


def call_cost(
    model: str, input_tokens: int, output_tokens: int, thinking_tokens: int | None = None
) -> Cost | None:
    """Estimated cost of one call, or None if the model's price is not known."""
    price = PRICES.get(model)
    if price is None:
        return None
    rate = price.rate_for(input_tokens)
    thinking = thinking_tokens or 0
    per = 1_000_000
    return Cost(
        input=input_tokens * rate.input_per_mtok / per,
        response=(output_tokens - thinking) * rate.output_per_mtok / per,
        thinking=thinking * rate.output_per_mtok / per,
    )


def usd(amount: float) -> str:
    """A dollar amount with enough precision to show small costs."""
    if amount == 0:
        return "$0.00"
    if amount < 0.01:
        return f"${amount:.4f}"
    if amount < 1:
        return f"${amount:.3f}"
    return f"${amount:,.2f}"
