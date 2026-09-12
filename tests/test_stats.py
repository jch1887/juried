import pytest

from juried.stats import (
    best_possible_lower_bound,
    describe_gate,
    gate_passes,
    required_passes,
    wilson_interval,
)


@pytest.mark.parametrize(
    ("passes", "runs", "lower", "upper"),
    [
        (10, 10, 0.7225, 1.0),
        (9, 10, 0.5958, 0.9821),
        (5, 10, 0.2366, 0.7634),
        (0, 10, 0.0, 0.2775),
        (80, 100, 0.7112, 0.8666),
        (1, 1, 0.2065, 1.0),
        (20, 20, 0.8389, 1.0),
        (29, 30, 0.8333, 0.9941),
    ],
)
def test_wilson_matches_published_values(
    passes: int, runs: int, lower: float, upper: float
) -> None:
    interval = wilson_interval(passes, runs)
    assert interval.lower == pytest.approx(lower, abs=1e-4)
    assert interval.upper == pytest.approx(upper, abs=1e-4)


def test_zero_runs_gives_empty_interval() -> None:
    assert wilson_interval(0, 0) == wilson_interval(0, 0)
    assert wilson_interval(0, 0).lower == 0.0
    assert wilson_interval(0, 0).upper == 0.0


def test_invalid_counts_rejected() -> None:
    with pytest.raises(ValueError):
        wilson_interval(11, 10)
    with pytest.raises(ValueError):
        wilson_interval(-1, 10)


def test_gate_uses_lower_bound() -> None:
    assert gate_passes(10, 10, 0.7)
    assert not gate_passes(10, 10, 0.8)
    assert not gate_passes(9, 10, 0.7)
    assert gate_passes(29, 30, 0.8)
    assert not gate_passes(28, 30, 0.8)
    assert not gate_passes(0, 0, 0.0)


def test_best_possible_lower_bound_grows_with_runs() -> None:
    assert best_possible_lower_bound(10) == pytest.approx(0.7225, abs=1e-4)
    assert best_possible_lower_bound(30) > best_possible_lower_bound(10)


@pytest.mark.parametrize(
    ("runs", "threshold", "needed"),
    [(10, 0.7, 10), (20, 0.7, 19), (30, 0.7, 26), (50, 0.7, 42), (10, 0.5, 9), (10, 0.9, None)],
)
def test_required_passes_matches_documented_table(
    runs: int, threshold: float, needed: int | None
) -> None:
    assert required_passes(runs, threshold) == needed
    assert required_passes(0, 0.5) is None


@pytest.mark.parametrize(
    ("runs", "threshold", "text"),
    [
        (10, 0.7, "gate needs 10/10 passes at threshold 0.70 (no misses tolerated)"),
        (20, 0.7, "gate needs 19/20 passes at threshold 0.70 (1 miss tolerated)"),
        (50, 0.7, "gate needs 42/50 passes at threshold 0.70 (8 misses tolerated)"),
    ],
)
def test_describe_gate_states_the_real_requirement(runs: int, threshold: float, text: str) -> None:
    assert describe_gate(runs, threshold) == text


def test_describe_gate_flags_unattainable_gate() -> None:
    assert describe_gate(10, 0.9).startswith("with 10 runs the best possible lower bound is 0.72")
