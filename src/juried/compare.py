from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Kinds that count as a regression, in the order they are printed.
REGRESSIONS = ("gate lost", "newly incomplete", "new failing", "dropped")
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


def scenarios_by_id(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    entries: dict[str, dict[str, Any]] = {}
    for criterion in report["criteria"]:
        for scenario in criterion.get("scenarios", []):
            entries[str(scenario["id"])] = scenario
    return entries


@dataclass(frozen=True)
class Change:
    id: str
    name: str
    criterion: str
    kind: str
    old: dict[str, Any] | None
    new: dict[str, Any] | None
    detail: str

    @property
    def regression(self) -> bool:
        return self.kind in REGRESSIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "criterion": self.criterion,
            "kind": self.kind,
            "regression": self.regression,
            "detail": self.detail,
            "old": summary(self.old),
            "new": summary(self.new),
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
        "gate_passed": entry.get("gate_passed"),
        "status": entry.get("status", "upheld" if entry.get("gate_passed") else "failed"),
    }


def describe(entry: dict[str, Any]) -> str:
    facts = summary(entry)
    assert facts is not None
    return (
        f"{facts['passes']}/{facts['judged']} rate {facts['pass_rate']:.2f} "
        f"lower {facts['lower']:.2f} {facts['status']}"
    )


def classify(
    old: dict[str, Any] | None, new: dict[str, Any] | None, tolerance: float
) -> tuple[str, str]:
    if old is None:
        assert new is not None
        if new.get("gate_passed"):
            return "added", f"new scenario, {describe(new)}"
        return "new failing", f"new scenario, {describe(new)}"
    if new is None:
        return "removed", f"no longer in the new report, was {describe(old)}"
    before, after = summary(old), summary(new)
    assert before is not None and after is not None
    detail = f"{describe(old)} -> {describe(new)}"
    # Errors are checked first: an incomplete scenario loses its gate too, but the cause is
    # the endpoint or the judge, not the feature's quality.
    if before["status"] != "incomplete" and after["status"] == "incomplete":
        return "newly incomplete", detail
    if before["gate_passed"] and not after["gate_passed"]:
        return "gate lost", detail
    if not before["gate_passed"] and after["gate_passed"]:
        return "gate regained", detail
    rate_drop = float(before["pass_rate"]) - float(after["pass_rate"])
    lower_drop = float(before["lower"]) - float(after["lower"])
    if rate_drop > tolerance or lower_drop > tolerance:
        return "dropped", detail
    if rate_drop < -tolerance or lower_drop < -tolerance:
        return "improved", detail
    return "unchanged", detail


def compare_reports(
    old: dict[str, Any], new: dict[str, Any], tolerance: float = 0.0
) -> list[Change]:
    before, after = scenarios_by_id(old), scenarios_by_id(new)
    changes: list[Change] = []
    for scenario_id in sorted(set(before) | set(after)):
        left, right = before.get(scenario_id), after.get(scenario_id)
        source = right if right is not None else left
        assert source is not None
        kind, detail = classify(left, right, tolerance)
        changes.append(
            Change(
                scenario_id,
                str(source.get("name", scenario_id)),
                str(source.get("criterion", "")),
                kind,
                left,
                right,
                detail,
            )
        )
    order = {kind: index for index, kind in enumerate(REGRESSIONS + IMPROVEMENTS + NEUTRAL)}
    return sorted(changes, key=lambda change: (order[change.kind], change.id))


def comparison_dict(
    old_path: Path, new_path: Path, changes: list[Change], tolerance: float
) -> dict[str, Any]:
    return {
        "tool": "juried",
        "old": str(old_path),
        "new": str(new_path),
        "tolerance": tolerance,
        "regressions": sum(1 for change in changes if change.regression),
        "changes": [change.to_dict() for change in changes if change.kind != "unchanged"],
        "unchanged": sum(1 for change in changes if change.kind == "unchanged"),
    }
