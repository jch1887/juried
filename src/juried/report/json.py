from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from juried import __version__
from juried.config import Config
from juried.criteria import Criterion
from juried.pricing import Usage, estimate_usd, prices_for
from juried.runner import ScenarioResult

# Bumped whenever a field in the JSON report or the calibration report changes meaning or
# goes away; adding a field does not bump it. Compare refuses reports of different versions.
SCHEMA_VERSION = 1


def usage_entry(usage: Usage, prices: tuple[float, float] | None) -> dict[str, Any]:
    return {**usage.to_dict(), "estimated_cost_usd": estimate_usd(usage, prices)}


def scenario_entry(result: ScenarioResult, prices: tuple[float, float] | None) -> dict[str, Any]:
    entry = result.to_dict()
    entry["usage"] = usage_entry(result.usage, prices)
    entry["failures"] = [
        {
            "attempt": run.attempt,
            "outcome": run.outcome,
            "transcript": [turn.model_dump() for turn in run.transcript],
            "response": run.response,
            "reason": run.reason,
            "model": run.verdict.model if run.verdict else None,
            "judged_at": run.verdict.judged_at if run.verdict else None,
            "agreement": run.agreement,
            "votes": len(run.votes),
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

    prices = prices_for(config.judge.model, config.judge.prices)
    gate = config.run.gate()
    criteria_entries = []
    for criterion_id, group in by_criterion.items():
        criterion = known[criterion_id]
        scenarios = [scenario_entry(result, prices) for result in group]
        criteria_entries.append(
            {
                "id": criterion.id,
                "title": criterion.title,
                "description": criterion.description,
                "scenarios": scenarios,
                "gates_passed": sum(1 for s in scenarios if s["gate_passed"]),
                "gates_failed": sum(1 for s in scenarios if s["status"] == "failed"),
                "incomplete": sum(1 for s in scenarios if s["status"] == "incomplete"),
            }
        )

    gates_passed = sum(1 for r in results if r.gate_passed)
    incomplete = sum(1 for r in results if not r.complete)
    return {
        "tool": "juried",
        "schema_version": SCHEMA_VERSION,
        "version": __version__,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "judge": {
            "provider": config.judge.provider,
            "model": config.judge.model,
            "temperature": config.judge.temperature,
            "votes": config.judge.votes,
            "prices_usd_per_million": None if prices is None else list(prices),
        },
        "defaults": {
            "runs": gate.runs,
            "misses": gate.misses,
            "required_passes": gate.passes_needed,
            "threshold": gate.equivalent_threshold,
        },
        "summary": {
            "criteria": len(criteria_entries),
            "scenarios": len(results),
            "gates_passed": gates_passed,
            "gates_failed": len(results) - gates_passed - incomplete,
            "incomplete": incomplete,
            "transport_errors": sum(r.transport_errors for r in results),
            "responses_from_cache": sum(r.responses_from_cache for r in results),
            "split_verdicts": sum(r.split_verdicts for r in results),
            "judge_errors": sum(r.judge_errors for r in results),
            "usage": usage_entry(sum((r.usage for r in results), Usage()), prices),
            "criteria_without_scenarios": [
                entry["id"] for entry in criteria_entries if not entry["scenarios"]
            ],
        },
        "criteria": criteria_entries,
    }


def write_json(report: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
