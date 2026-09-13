import pytest

from juried.pricing import (
    PRICES,
    PRICES_DATED,
    TargetUsage,
    Usage,
    describe_run_cost,
    describe_target_usage,
    describe_usage,
    estimate_usd,
    format_usd,
    prices_for,
    target_estimate_usd,
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


def test_target_usage_adds_and_prices() -> None:
    total = sum([TargetUsage(1, 400, 100, 900, 1), TargetUsage(1, 0, 0, 20, 0)], TargetUsage())
    assert total == TargetUsage(2, 400, 100, 920, 1)
    assert total.to_dict() == {
        "requests": 2,
        "input_tokens": 400,
        "output_tokens": 100,
        "bytes": 920,
        "counted": 1,
    }
    assert target_estimate_usd(total, None, None) is None
    assert target_estimate_usd(total, None, 0.002) == pytest.approx(0.004)
    assert target_estimate_usd(total, (2.0, 10.0), None) == pytest.approx(0.0018)
    assert target_estimate_usd(total, (2.0, 10.0), 0.002) == pytest.approx(0.004)


def test_describe_target_usage_has_three_forms() -> None:
    usage = TargetUsage(260, 84_100, 12_300, 500_000, 260)
    assert describe_target_usage(usage, (2.0, 10.0), None) == (
        "260 requests, 84,100 input + 12,300 output tokens, estimated $0.29 at configured prices"
    )
    assert (
        describe_target_usage(usage, None, 0.002) == "260 requests, estimated $0.52 at $0.0020 each"
    )
    assert describe_target_usage(usage, None, None) == (
        "260 requests, cost unknown (set [target] input_price/output_price or cost_per_request)"
    )
    partial = TargetUsage(4, 800, 200, 0, 2)
    assert "800 input + 200 output tokens over 2 of them" in describe_target_usage(
        partial, (2.0, 10.0), None
    )
    assert describe_target_usage(TargetUsage(1), None, None).startswith("1 request, ")


def test_describe_run_cost_needs_both_sides() -> None:
    assert describe_run_cost(0.11, 0.43) == "estimated run cost: $0.54 (judge $0.11 + target $0.43)"
    assert describe_run_cost(None, 0.43) is None
    assert describe_run_cost(0.11, None) is None


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
