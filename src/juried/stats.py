from __future__ import annotations

import math
from dataclasses import dataclass

Z_95 = 1.959963984540054

# Failed attempts a scenario may have and still pass, when neither misses nor the
# deprecated threshold is set. With the default 20 runs the gate needs 19 passes; a single
# run, as in a smoke test, must pass, since a gate that tolerates every attempt failing
# would never fail.
DEFAULT_MISSES = 1


@dataclass(frozen=True)
class Interval:
    lower: float
    upper: float


def wilson_interval(passes: int, runs: int, z: float = Z_95) -> Interval:
    if runs < 0 or passes < 0 or passes > runs:
        raise ValueError(f"invalid counts: passes={passes} runs={runs}")
    if runs == 0:
        return Interval(0.0, 0.0)
    p = passes / runs
    z2 = z * z
    denominator = 1 + z2 / runs
    centre = (p + z2 / (2 * runs)) / denominator
    half_width = z * math.sqrt(p * (1 - p) / runs + z2 / (4 * runs * runs)) / denominator
    return Interval(max(0.0, centre - half_width), min(1.0, centre + half_width))


def best_possible_lower_bound(runs: int) -> float:
    return wilson_interval(runs, runs).lower


# The threshold rule from before 0.3: the gate held when the interval's lower bound met the
# threshold. It survives only to derive misses from a deprecated threshold.
def required_passes(runs: int, threshold: float) -> int | None:
    for passes in range(runs + 1):
        if runs > 0 and wilson_interval(passes, runs).lower >= threshold:
            return passes
    return None


def misses_from_threshold(runs: int, threshold: float) -> int | None:
    needed = required_passes(runs, threshold)
    return None if needed is None else runs - needed


class GateError(ValueError):
    pass


@dataclass(frozen=True)
class Gate:
    runs: int
    misses: int
    # The deprecated threshold this gate was derived from, when there was one.
    threshold: float | None = None

    @property
    def passes_needed(self) -> int:
        return self.runs - self.misses

    def met(self, passes: int, judged: int) -> bool:
        return judged > 0 and judged - passes <= self.misses

    # The lower bound the gate is equivalent to: the interval of the smallest passing score.
    # Kept for reports and dashboards that plotted the threshold.
    @property
    def equivalent_threshold(self) -> float:
        if self.threshold is not None:
            return self.threshold
        return round(wilson_interval(self.passes_needed, self.runs).lower, 4)

    def describe(self) -> str:
        if self.misses == 0:
            tolerated = "no misses tolerated"
        else:
            tolerated = f"{self.misses} miss{'es' if self.misses != 1 else ''} tolerated"
        return f"gate needs {self.passes_needed}/{self.runs} passes ({tolerated})"


def build_gate(runs: int, misses: int | None, threshold: float | None) -> Gate:
    if runs < 1:
        raise GateError(f"runs must be at least 1, not {runs}")
    if threshold is not None:
        derived = misses_from_threshold(runs, threshold)
        if derived is None:
            raise GateError(
                f"threshold {threshold:.2f} can never be met with {runs} runs, whose best "
                f"possible lower bound is {best_possible_lower_bound(runs):.2f}; threshold is "
                "deprecated, set misses instead"
            )
        if misses is None:
            misses = derived
        elif misses != derived:
            raise GateError(
                f"misses = {misses} and threshold = {threshold} disagree: with {runs} runs "
                f"the threshold tolerates {derived} miss{'es' if derived != 1 else ''}; "
                "remove threshold, it is deprecated"
            )
    if misses is None:
        misses = min(DEFAULT_MISSES, runs - 1)
    if misses >= runs:
        raise GateError(
            f"misses = {misses} is not below runs = {runs}, so the gate could never fail; "
            "lower misses or raise runs"
        )
    return Gate(runs, misses, threshold)


def deprecation_notice(gate: Gate) -> str | None:
    if gate.threshold is None:
        return None
    return (
        f"threshold is deprecated and is removed in 0.4: threshold {gate.threshold} with "
        f"{gate.runs} runs tolerates {gate.misses} miss{'es' if gate.misses != 1 else ''}, "
        f"so set misses = {gate.misses} instead"
    )
