from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from juried.stats import (
    Interval,
    detectable_drop,
    fisher_decrease_p,
    newcombe_difference,
    required_passes,
)

# Bumped when a field in the comparison JSON changes meaning or goes away.
COMPARE_SCHEMA_VERSION = 1
DEFAULT_ALPHA = 0.05
DEFAULT_MIN_EFFECT = 0.10
# The pass rate the power note assumes the feature started from.
POWER_BASELINE = 0.9

# Kinds that count as a regression, in the order they are printed.
REGRESSIONS = ("gate lost", "newly incomplete", "new failing", "dropped")
NOISE = ("drop within noise",)
IMPROVEMENTS = ("gate regained", "improved")
NEUTRAL = ("added", "removed", "unchanged")


class CompareError(Exception):
    pass


def load_report(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CompareError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CompareError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or data.get("tool") != "juried" or "criteria" not in data:
        raise CompareError(f"{path} is not a juried report")
    return data


def schema_version(report: dict[str, Any]) -> int | None:
    value = report.get("schema_version")
    return int(value) if isinstance(value, int) else None


def check_same_schema(
    old_path: Path, old: dict[str, Any], new_path: Path, new: dict[str, Any]
) -> None:
    before, after = schema_version(old), schema_version(new)
    if before == after:
        return

    def describe(path: Path, version: int | None) -> str:
        if version is None:
            return f"{path} has no schema_version (written before juried 0.2.0)"
        return f"{path} has schema_version {version}"

    raise CompareError(
        f"cannot compare reports with different schemas: {describe(old_path, before)}, "
        f"{describe(new_path, after)}. Re-run the older side with this version of juried."
    )


def scenarios_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for criterion in report["criteria"]:
        for scenario in criterion.get("scenarios", []):
            entries[str(scenario["id"])] = scenario
    return entries


@dataclass(frozen=True)
class Difference:
    # New rate minus old rate, so a drop is negative, with its Newcombe 95% interval.
    diff: float
    interval: Interval
    # One sided Fisher p-value for a decrease, and whether it clears alpha and min_effect.
    p_value: float
    significant: bool

    def describe(self) -> str:
        return (
            f"diff {self.diff:+.2f} ({self.interval.lower:+.2f} to {self.interval.upper:+.2f}), "
            f"p {self.p_value:.3f}"
        )


def difference(
    old: dict[str, Any], new: dict[str, Any], alpha: float, min_effect: float
) -> Difference:
    a, n1 = int(old.get("passes") or 0), int(old.get("judged", old.get("runs")) or 0)
    b, n2 = int(new.get("passes") or 0), int(new.get("judged", new.get("runs")) or 0)
    p1 = a / n1 if n1 else 0.0
    p2 = b / n2 if n2 else 0.0
    p_value = fisher_decrease_p(a, n1, b, n2)
    diff = p2 - p1
    significant = p_value < alpha and -diff >= min_effect
    return Difference(diff, newcombe_difference(a, n1, b, n2), p_value, significant)


@dataclass(frozen=True)
class Change:
    id: str
    name: str
    criterion: str
    kind: str
    old: dict[str, Any] | None
    new: dict[str, Any] | None
    detail: str
    difference: Difference | None = None

    @property
    def regression(self) -> bool:
        return self.kind in REGRESSIONS

    def to_dict(self) -> dict[str, Any]:
        diff = self.difference
        return {
            "id": self.id,
            "name": self.name,
            "criterion": self.criterion,
            "kind": self.kind,
            "regression": self.regression,
            "detail": self.detail,
            "old": summary(self.old),
            "new": summary(self.new),
            "p_value": None if diff is None else round(diff.p_value, 6),
            "diff": None if diff is None else round(diff.diff, 4),
            "diff_interval": (
                None
                if diff is None
                else {
                    "lower": round(diff.interval.lower, 4),
                    "upper": round(diff.interval.upper, 4),
                }
            ),
            "significant": None if diff is None else diff.significant,
        }


def summary(entry: dict[str, Any] | None) -> dict[str, Any] | None:
    if entry is None:
        return None
    return {
        "passes": entry.get("passes"),
        "judged": entry.get("judged", entry.get("runs")),
        "runs": entry.get("runs"),
        "pass_rate": entry.get("pass_rate"),
        "lower": entry.get("interval", {}).get("lower"),
        "passes_needed": passes_needed(entry),
        "gate_passed": entry.get("gate_passed"),
        "status": entry.get("status", "upheld" if entry.get("gate_passed") else "failed"),
    }


# Reports from 0.3 onwards carry misses; earlier ones carry a threshold and, from 0.2.0,
# required_passes. Any of the three says what the gate needed.
def passes_needed(entry: dict[str, Any]) -> int | None:
    runs = entry.get("runs")
    if not isinstance(runs, int):
        return None
    if isinstance(entry.get("misses"), int):
        return runs - int(entry["misses"])
    if isinstance(entry.get("required_passes"), int):
        return int(entry["required_passes"])
    if isinstance(entry.get("threshold"), int | float):
        return required_passes(runs, float(entry["threshold"]))
    return None


def describe(entry: dict[str, Any]) -> str:
    facts = summary(entry)
    assert facts is not None
    return f"{facts['passes']}/{facts['judged']} rate {facts['pass_rate']:.2f} {facts['status']}"


def classify(
    old: dict[str, Any] | None,
    new: dict[str, Any] | None,
    alpha: float,
    min_effect: float,
) -> tuple[str, str, Difference | None]:
    if old is None:
        assert new is not None
        if new.get("gate_passed"):
            return "added", f"new scenario, {describe(new)}", None
        return "new failing", f"new scenario, {describe(new)}", None
    if new is None:
        return "removed", f"no longer in the new report, was {describe(old)}", None
    before, after = summary(old), summary(new)
    assert before is not None and after is not None
    diff = difference(old, new, alpha, min_effect)
    detail = f"{describe(old)} -> {describe(new)}, {diff.describe()}"
    # Errors are checked first: an incomplete scenario loses its gate too, but the cause is
    # the endpoint or the judge, not the feature's quality.
    if before["status"] != "incomplete" and after["status"] == "incomplete":
        return "newly incomplete", detail, diff
    if before["gate_passed"] and not after["gate_passed"]:
        return "gate lost", detail, diff
    if not before["gate_passed"] and after["gate_passed"]:
        return "gate regained", detail, diff
    if diff.significant:
        return "dropped", detail, diff
    if diff.diff < 0:
        return "drop within noise", detail, diff
    # A rise is held to the same standard, with the test turned round.
    rise = difference(new, old, alpha, min_effect)
    if rise.significant:
        return "improved", detail, diff
    return "unchanged", detail, diff


def compare_reports(
    old: dict[str, Any],
    new: dict[str, Any],
    alpha: float = DEFAULT_ALPHA,
    min_effect: float = DEFAULT_MIN_EFFECT,
) -> list[Change]:
    before, after = scenarios_by_id(old), scenarios_by_id(new)
    changes: list[Change] = []
    for scenario_id in sorted(set(before) | set(after)):
        left, right = before.get(scenario_id), after.get(scenario_id)
        source = right if right is not None else left
        assert source is not None
        kind, detail, diff = classify(left, right, alpha, min_effect)
        changes.append(
            Change(
                scenario_id,
                str(source.get("name", scenario_id)),
                str(source.get("criterion", "")),
                kind,
                left,
                right,
                detail,
                diff,
            )
        )
    kinds = REGRESSIONS + NOISE + IMPROVEMENTS + NEUTRAL
    order = {kind: index for index, kind in enumerate(kinds)}
    return sorted(changes, key=lambda change: (order[change.kind], change.id))


@dataclass(frozen=True)
class Power:
    old_judged: int
    new_judged: int
    drop: float

    def describe(self, alpha: float) -> str:
        points = round(self.drop * 100)
        return (
            f"note: at {self.old_judged} vs {self.new_judged} runs this comparison can only "
            f"detect drops of about {points} points or more (80% power at alpha {alpha:g} from "
            f"a {POWER_BASELINE:.0%} pass rate); raise runs to see smaller regressions"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "old_judged": self.old_judged,
            "new_judged": self.new_judged,
            "drop": round(self.drop, 4),
        }


# The smallest scenario on each side bounds what the whole comparison can show.
def power_of(changes: list[Change], alpha: float) -> Power | None:
    paired = [change for change in changes if change.old is not None and change.new is not None]
    if not paired:
        return None
    olds = [int((summary(c.old) or {}).get("judged") or 0) for c in paired]
    news = [int((summary(c.new) or {}).get("judged") or 0) for c in paired]
    old_judged, new_judged = min(olds), min(news)
    return Power(
        old_judged, new_judged, detectable_drop(old_judged, new_judged, alpha, POWER_BASELINE)
    )


def comparison_dict(
    old_path: Path,
    new_path: Path,
    changes: list[Change],
    alpha: float,
    min_effect: float,
) -> dict[str, Any]:
    power = power_of(changes, alpha)
    return {
        "tool": "juried",
        "schema_version": COMPARE_SCHEMA_VERSION,
        "old": str(old_path),
        "new": str(new_path),
        "alpha": alpha,
        "min_effect": min_effect,
        "detectable_drop": None if power is None else power.to_dict(),
        "regressions": sum(1 for change in changes if change.regression),
        "within_noise": sum(1 for change in changes if change.kind in NOISE),
        "changes": [change.to_dict() for change in changes if change.kind != "unchanged"],
        "unchanged": sum(1 for change in changes if change.kind == "unchanged"),
    }
