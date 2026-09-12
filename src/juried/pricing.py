from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# List prices in US dollars per million tokens, (input, output), as published in
# September 2026. They drift, so a run states the date and [judge] can override them.
PRICES_DATED = "2026-09"
PRICES: dict[str, tuple[float, float]] = {
    "stub": (0.0, 0.0),
    "claude-haiku-4-5": (1.00, 5.00),
    "claude-sonnet-4-6": (3.00, 15.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-opus-4-6": (5.00, 25.00),
    "claude-opus-4-7": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-fable-5": (10.00, 50.00),
    "claude-fable-5-1": (10.00, 50.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-nano": (0.10, 0.40),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-5-nano": (0.05, 0.40),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
}


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    calls: int = 0

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            self.input_tokens + other.input_tokens,
            self.output_tokens + other.output_tokens,
            self.calls + other.calls,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "calls": self.calls,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> Usage:
        if not data:
            return cls()
        return cls(int(data.get("input_tokens", 0)), int(data.get("output_tokens", 0)), 1)


def prices_for(
    model: str, override: tuple[float, float] | None = None
) -> tuple[float, float] | None:
    if override is not None:
        return override
    if model in PRICES:
        return PRICES[model]
    # A dated snapshot such as claude-sonnet-5-20260401 prices like its base model.
    matches = [key for key in PRICES if model.startswith(key + "-")]
    return PRICES[max(matches, key=len)] if matches else None


def estimate_usd(usage: Usage, prices: tuple[float, float] | None) -> float | None:
    if prices is None:
        return None
    input_price, output_price = prices
    return (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000


def format_usd(amount: float) -> str:
    return f"${amount:.4f}" if amount < 0.1 else f"${amount:.2f}"


def describe_usage(usage: Usage, model: str, override: tuple[float, float] | None) -> str:
    text = (
        f"{usage.input_tokens:,} input + {usage.output_tokens:,} output tokens "
        f"over {usage.calls} call(s)"
    )
    prices = prices_for(model, override)
    cost = estimate_usd(usage, prices)
    if cost is None:
        return (
            f"{text}; no list price known for {model}, set input_price and output_price "
            "under [judge] in juried.toml (US dollars per million tokens) for an estimate"
        )
    source = "configured prices" if override is not None else f"list prices of {PRICES_DATED}"
    return f"{text}, estimated {format_usd(cost)} at {source}"
