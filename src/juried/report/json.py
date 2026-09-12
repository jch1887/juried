from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from juried import __version__
from juried.config import Config
from juried.criteria import Criterion
from juried.runner import ScenarioResult


def scenario_entry(result: ScenarioResult) -> dict[str, Any]:
    entry = result.to_dict()
    entry["failures"] = [
        {
            "attempt": run.attempt,
            "outcome": run.outcome,
            "response": run.response,
            "reason": run.reason,
            "model": run.verdict.model if run.verdict else None,
            "judged_at": run.verdict.judged_at if run.verdict else None,
        }
        for run in result.failures
    ]
    return entry


def build_report(
    config: Config, criteria: Sequence[Criterion], results: Sequence[ScenarioResult]
) -> dict[str, Any]:
    by_criterion: dict[str, list[ScenarioResult]] = {c.id: [] for c in criteria}
    for result in results:
        by_criterion.setdefault(result.criterion.id, []).append(result)
    known = {c.id: c for c in criteria}
    for result in results:
        known.setdefault(result.criterion.id, result.criterion)

    criteria_entries = []
    for criterion_id, group in by_criterion.items():
        criterion = known[criterion_id]
        scenarios = [scenario_entry(result) for result in group]
        criteria_entries.append(
            {
                "id": criterion.id,
                "title": criterion.title,
                "description": criterion.description,
                "scenarios": scenarios,
                "gates_passed": sum(1 for s in scenarios if s["gate_passed"]),
                "gates_failed": sum(1 for s in scenarios if not s["gate_passed"]),
            }
        )

    gates_passed = sum(1 for r in results if r.gate_passed)
    return {
        "tool": "juried",
        "version": __version__,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "judge": {
            "provider": config.judge.provider,
            "model": config.judge.model,
            "temperature": config.judge.temperature,
        },
        "defaults": {"runs": config.run.runs, "threshold": config.run.threshold},
        "summary": {
            "criteria": len(criteria_entries),
            "scenarios": len(results),
            "gates_passed": gates_passed,
            "gates_failed": len(results) - gates_passed,
            "transport_errors": sum(r.transport_errors for r in results),
            "criteria_without_scenarios": [
                entry["id"] for entry in criteria_entries if not entry["scenarios"]
            ],
        },
        "criteria": criteria_entries,
    }


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
