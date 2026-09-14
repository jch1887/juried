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

    # The pass rate the miss count amounts to, for a gate on the corrected rate.
    @property
    def implied_rate(self) -> float:
        return self.passes_needed / self.runs

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


# One sided Fisher's exact test that the new pass rate is lower than the old one. Given the
# totals, the number of new passes is hypergeometric; the p-value is the probability of
# seeing that few new passes or fewer. Exact in integers, so it holds for any run count.
def fisher_decrease_p(old_passes: int, old_judged: int, new_passes: int, new_judged: int) -> float:
    for passes, judged in ((old_passes, old_judged), (new_passes, new_judged)):
        if judged < 0 or passes < 0 or passes > judged:
            raise ValueError(f"invalid counts: passes={passes} judged={judged}")
    if old_judged == 0 or new_judged == 0:
        return 1.0
    total = old_judged + new_judged
    successes = old_passes + new_passes
    lowest = max(0, successes - old_judged)
    tail = sum(
        math.comb(successes, x) * math.comb(total - successes, new_judged - x)
        for x in range(lowest, new_passes + 1)
    )
    return tail / math.comb(total, new_judged)


# Newcombe's hybrid score interval for new minus old: the Wilson bounds of each side are
# combined, so it behaves at the edges (0 or all passes) where a normal interval does not.
def newcombe_difference(
    old_passes: int, old_judged: int, new_passes: int, new_judged: int, z: float = Z_95
) -> Interval:
    if old_judged == 0 or new_judged == 0:
        return Interval(-1.0, 1.0)
    p1, p2 = old_passes / old_judged, new_passes / new_judged
    w1, w2 = wilson_interval(old_passes, old_judged, z), wilson_interval(new_passes, new_judged, z)
    diff = p2 - p1
    lower = diff - math.sqrt((p2 - w2.lower) ** 2 + (w1.upper - p1) ** 2)
    upper = diff + math.sqrt((w2.upper - p2) ** 2 + (p1 - w1.lower) ** 2)
    return Interval(max(-1.0, lower), min(1.0, upper))


def normal_quantile(probability: float) -> float:
    # Acklam's rational approximation, accurate to about 1e-9, enough for a power note.
    if not 0.0 < probability < 1.0:
        raise ValueError(f"probability must be strictly between 0 and 1, not {probability}")
    a = (
        -3.969683028665376e01,
        2.209460984245205e02,
        -2.759285104469687e02,
        1.383577518672690e02,
        -3.066479806614716e01,
        2.506628277459239e00,
    )
    b = (
        -5.447609879822406e01,
        1.615858368580409e02,
        -1.556989798598866e02,
        6.680131188771972e01,
        -1.328068155288572e01,
    )
    c = (
        -7.784894002430293e-03,
        -3.223964580411365e-01,
        -2.400758277161838e00,
        -2.549732539343734e00,
        4.374664141464968e00,
        2.938163982698783e00,
    )
    d = (7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e00, 3.754408661907416e00)
    p = probability
    if p < 0.02425:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0] * q + c[1]) * q + c[2]) * q + c[3]) * q + c[4]) * q + c[5]) / (
            (((d[0] * q + d[1]) * q + d[2]) * q + d[3]) * q + 1
        )
    if p > 1 - 0.02425:
        return -normal_quantile(1 - p)
    q = p - 0.5
    r = q * q
    return (
        (((((a[0] * r + a[1]) * r + a[2]) * r + a[3]) * r + a[4]) * r + a[5])
        * q
        / (((((b[0] * r + b[1]) * r + b[2]) * r + b[3]) * r + b[4]) * r + 1)
    )


# The smallest drop from `baseline` that a one sided two proportion test at `alpha` would
# detect with `power`, by the usual normal approximation. Approximate by design: it tells a
# reader what size of regression the run counts can show, not what any one test found.
def detectable_drop(
    old_judged: int, new_judged: int, alpha: float, baseline: float = 0.9, power: float = 0.8
) -> float:
    if old_judged <= 0 or new_judged <= 0:
        return 1.0
    z_alpha, z_power = normal_quantile(1 - alpha), normal_quantile(power)

    def needed(drop: float) -> float:
        p1, p2 = baseline, baseline - drop
        pooled = (p1 * old_judged + p2 * new_judged) / (old_judged + new_judged)
        null = math.sqrt(pooled * (1 - pooled) * (1 / old_judged + 1 / new_judged))
        alternative = math.sqrt(p1 * (1 - p1) / old_judged + p2 * (1 - p2) / new_judged)
        return z_alpha * null + z_power * alternative

    low, high = 0.0, baseline
    if needed(high) > high:
        return 1.0
    for _ in range(60):
        mid = (low + high) / 2
        if needed(mid) > mid:
            low = mid
        else:
            high = mid
    return high
