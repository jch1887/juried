from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from juried.config import Config
from juried.pricing import (
    ASSUMED_INPUT_TOKENS,
    ASSUMED_OUTPUT_TOKENS,
    PRICES_DATED,
    TargetUsage,
    Usage,
    describe_run_cost,
    estimate_usd,
    format_usd,
    prices_for,
    target_estimate_usd,
)
from juried.scenarios import Scenario


@dataclass(frozen=True)
class TokenGuess:
    input_tokens: int
    output_tokens: int
    # Where the per call figures came from: "assumed" or "the last report".
    source: str

    @property
    def describe(self) -> str:
        return (
            f"{self.input_tokens} input + {self.output_tokens} output tokens per call "
            f"({self.source})"
        )


ASSUMED = TokenGuess(ASSUMED_INPUT_TOKENS, ASSUMED_OUTPUT_TOKENS, "assumed")


@dataclass(frozen=True)
class Plan:
    scenarios: int
    attempts: int
    with_turns: int
    target_requests: int
    judge_calls: int
    votes: int
    judge_model: str
    judge_tokens: TokenGuess
    judge_price_source: str | None
    judge_cost: float | None
    target_tokens: TokenGuess
    target_cost_per_request: float | None
    target_cost: float | None

    @property
    def total(self) -> float | None:
        if self.judge_cost is None or self.target_cost is None:
            return None
        return self.judge_cost + self.target_cost


def load_previous_report(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("tool") == "juried" else None


def average_tokens(entry: Any, calls_key: str, counted_key: str | None = None) -> TokenGuess | None:
    if not isinstance(entry, dict):
        return None
    calls = entry.get(counted_key or calls_key)
    if not isinstance(calls, int) or calls <= 0:
        return None
    inputs, outputs = entry.get("input_tokens"), entry.get("output_tokens")
    if not isinstance(inputs, int) or not isinstance(outputs, int):
        return None
    return TokenGuess(round(inputs / calls), round(outputs / calls), "the last report")


# Whole calls are counted, not tokens: each attempt drives every turn and the final message
# to the target once, and sends the response to the judge once per vote.
def plan_run(
    config: Config, scenarios: Sequence[Scenario], previous: dict[str, Any] | None
) -> Plan:
    attempts = 0
    target_requests = 0
    with_turns = 0
    for scenario in scenarios:
        runs = config.run.gate(scenario.runs, scenario.misses, scenario.threshold).runs
        attempts += runs
        target_requests += runs * (len(scenario.turns) + 1)
        with_turns += 1 if scenario.turns else 0
    votes = config.judge.votes
    judge_calls = attempts * votes

    usage = (previous or {}).get("summary", {}).get("usage", {})
    judge_tokens = average_tokens(usage.get("judge"), "calls") or ASSUMED
    target_tokens = average_tokens(usage.get("target"), "requests", "counted") or ASSUMED

    judge_prices = prices_for(config.judge.model, config.judge.prices)
    judge_cost = estimate_usd(
        Usage(judge_calls * judge_tokens.input_tokens, judge_calls * judge_tokens.output_tokens),
        judge_prices,
    )
    if judge_prices is None:
        judge_price_source = None
    elif config.judge.prices is not None:
        judge_price_source = "configured prices"
    else:
        judge_price_source = f"list prices of {PRICES_DATED}"

    target = config.target
    target_cost = target_estimate_usd(
        TargetUsage(
            target_requests,
            target_requests * target_tokens.input_tokens,
            target_requests * target_tokens.output_tokens,
        ),
        target.prices,
        target.cost_per_request,
    )
    return Plan(
        len(scenarios),
        attempts,
        with_turns,
        target_requests,
        judge_calls,
        votes,
        config.judge.model,
        judge_tokens,
        judge_price_source,
        judge_cost,
        target_tokens,
        target.cost_per_request,
        target_cost,
    )


def describe_plan(plan: Plan, config: Config) -> list[str]:
    lines = [
        "juried: dry run, nothing is sent",
        f"{plan.scenarios} scenario{'s' if plan.scenarios != 1 else ''} under "
        f"{config.criteria.scenarios_dir}, {plan.attempts} attempts in all"
        + (f", {plan.with_turns} with live turns" if plan.with_turns else ""),
    ]
    requests = (
        f"target: {plan.target_requests} requests (one per turn and final message per attempt)"
    )
    if plan.target_cost_per_request is not None:
        requests += (
            f", estimated {format_usd(plan.target_cost or 0.0)} at "
            f"{format_usd(plan.target_cost_per_request)} each"
        )
    elif plan.target_cost is not None:
        requests += (
            f", estimated {format_usd(plan.target_cost)} at {plan.target_tokens.describe} and "
            "configured prices"
        )
    else:
        requests += ", cost unknown (set [target] input_price/output_price or cost_per_request)"
    lines.append(requests)
    votes = f" x {plan.votes} votes" if plan.votes > 1 else ""
    calls = (
        f"judge: {plan.judge_calls} calls ({plan.attempts} attempts{votes}) to {plan.judge_model}"
    )
    if plan.judge_cost is not None:
        calls += (
            f", estimated {format_usd(plan.judge_cost)} at {plan.judge_tokens.describe} and "
            f"{plan.judge_price_source}"
        )
    else:
        calls += f", cost unknown (no list price for {plan.judge_model}; set [judge] prices)"
    lines.append(calls)
    run_cost = describe_run_cost(plan.judge_cost, plan.target_cost)
    lines.append(run_cost or "estimated run cost: unknown until both sides are priced")
    if "assumed" in (plan.judge_tokens.source, plan.target_tokens.source):
        lines.append(
            "note: assumed token counts are a placeholder; a run reports the real figures and the "
            "next dry run uses its averages"
        )
    return lines
