from __future__ import annotations

import math
from dataclasses import dataclass

Z_95 = 1.959963984540054


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


def gate_passes(passes: int, runs: int, threshold: float) -> bool:
    return runs > 0 and wilson_interval(passes, runs).lower >= threshold
