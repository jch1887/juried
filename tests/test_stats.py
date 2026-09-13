import pytest

from juried.stats import (
    Gate,
    GateError,
    best_possible_lower_bound,
    build_gate,
    deprecation_notice,
    misses_from_threshold,
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


def test_gate_counts_misses() -> None:
    gate = Gate(20, 1)
    assert gate.passes_needed == 19
    assert gate.met(19, 20)
    assert gate.met(20, 20)
    assert not gate.met(18, 20)
    # On an incomplete scenario the gate is judged on the attempts that reached a verdict.
    assert gate.met(17, 18)
    assert not gate.met(16, 18)
    assert not gate.met(0, 0)
    assert Gate(10, 0).met(10, 10)
    assert not Gate(10, 0).met(9, 10)


def test_equivalent_threshold_is_the_smallest_passing_lower_bound() -> None:
    assert Gate(20, 1).equivalent_threshold == pytest.approx(0.7639, abs=1e-4)
    assert Gate(10, 0).equivalent_threshold == pytest.approx(0.7225, abs=1e-4)
    assert Gate(20, 1, threshold=0.7).equivalent_threshold == 0.7


def test_best_possible_lower_bound_grows_with_runs() -> None:
    assert best_possible_lower_bound(10) == pytest.approx(0.7225, abs=1e-4)
    assert best_possible_lower_bound(30) > best_possible_lower_bound(10)


@pytest.mark.parametrize(
    ("runs", "threshold", "needed"),
    [(10, 0.7, 10), (20, 0.7, 19), (30, 0.7, 26), (50, 0.7, 42), (10, 0.5, 9), (10, 0.9, None)],
)
def test_misses_derive_from_the_old_threshold_table(
    runs: int, threshold: float, needed: int | None
) -> None:
    assert required_passes(runs, threshold) == needed
    assert misses_from_threshold(runs, threshold) == (None if needed is None else runs - needed)
    assert required_passes(0, 0.5) is None


def test_build_gate_precedence() -> None:
    assert build_gate(20, None, None) == Gate(20, 1)
    assert build_gate(50, None, None) == Gate(50, 1)
    assert build_gate(1, None, None) == Gate(1, 0)
    assert build_gate(20, 3, None) == Gate(20, 3)
    assert build_gate(20, 0, None) == Gate(20, 0)
    assert build_gate(50, None, 0.7) == Gate(50, 8, 0.7)
    assert build_gate(20, 1, 0.7) == Gate(20, 1, 0.7)
    with pytest.raises(GateError, match=r"misses = 3 and threshold = 0.7 disagree: with 20 runs"):
        build_gate(20, 3, 0.7)
    with pytest.raises(GateError, match=r"threshold 0.90 can never be met with 10 runs"):
        build_gate(10, None, 0.9)
    with pytest.raises(GateError, match=r"misses = 4 is not below runs = 4"):
        build_gate(4, 4, None)
    with pytest.raises(GateError, match=r"runs must be at least 1"):
        build_gate(0, None, None)


@pytest.mark.parametrize(
    ("gate", "text"),
    [
        (Gate(10, 0), "gate needs 10/10 passes (no misses tolerated)"),
        (Gate(20, 1), "gate needs 19/20 passes (1 miss tolerated)"),
        (Gate(50, 8), "gate needs 42/50 passes (8 misses tolerated)"),
    ],
)
def test_describe_gate_states_the_requirement_without_a_threshold(gate: Gate, text: str) -> None:
    assert gate.describe() == text
    assert "threshold" not in text


def test_deprecation_notice_names_the_derived_value() -> None:
    assert deprecation_notice(Gate(20, 1)) is None
    assert deprecation_notice(build_gate(50, None, 0.7)) == (
        "threshold is deprecated and is removed in 0.4: threshold 0.7 with 50 runs tolerates "
        "8 misses, so set misses = 8 instead"
    )
