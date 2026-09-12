import pytest

from juried.pricing import (
    PRICES,
    PRICES_DATED,
    Usage,
    describe_usage,
    estimate_usd,
    format_usd,
    prices_for,
)


def test_usage_adds_and_serialises() -> None:
    total = sum([Usage(10, 2, 1), Usage(5, 1, 1)], Usage())
    assert total == Usage(15, 3, 2)
    assert total.to_dict() == {"input_tokens": 15, "output_tokens": 3, "calls": 2}
    assert Usage.from_dict({"input_tokens": 4, "output_tokens": 1}) == Usage(4, 1, 1)
    assert Usage.from_dict(None) == Usage()


def test_prices_exact_prefix_override_and_unknown() -> None:
    assert prices_for("claude-sonnet-5") == PRICES["claude-sonnet-5"]
    assert prices_for("claude-sonnet-5-20260401") == PRICES["claude-sonnet-5"]
    assert prices_for("gpt-4.1-mini-2026-01-01") == PRICES["gpt-4.1-mini"]
    assert prices_for("gpt-4.1") == PRICES["gpt-4.1"]
    assert prices_for("gpt-4.1-nano") != PRICES["gpt-4.1"]
    assert prices_for("llama-9") is None
    assert prices_for("llama-9", (1.0, 2.0)) == (1.0, 2.0)
    assert prices_for("claude-sonnet-5", (0.5, 0.5)) == (0.5, 0.5)


def test_estimate_and_format() -> None:
    assert estimate_usd(Usage(1_000_000, 100_000, 3), (2.0, 10.0)) == pytest.approx(3.0)
    assert estimate_usd(Usage(10, 10, 1), None) is None
    assert format_usd(0.01234) == "$0.0123"
    assert format_usd(3.456) == "$3.46"


def test_describe_usage_states_source_or_asks_for_prices() -> None:
    text = describe_usage(Usage(41_220, 2_860, 130), "claude-sonnet-5", None)
    assert text.startswith("41,220 input + 2,860 output tokens over 130 call(s), estimated $0.11")
    assert text.endswith(f"at list prices of {PRICES_DATED}")
    assert describe_usage(Usage(1, 1, 1), "claude-sonnet-5", (0.0, 0.0)).endswith(
        "estimated $0.0000 at configured prices"
    )
    unknown = describe_usage(Usage(1, 1, 1), "llama-9", None)
    assert "no list price known for llama-9" in unknown
    assert "input_price and output_price" in unknown
