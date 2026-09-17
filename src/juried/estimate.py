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
class Expected:
    """What early stopping is expected to leave of the plan: attempts, requests and calls
    from a dynamic programme over each scenario's pass rate in the previous report."""

    attempts: float
    target_requests: float
    judge_calls: float
    judge_cost: float | None
    target_cost: float | None
    # Scenarios whose pass rate the previous report supplied; the rest are planned in full.
    with_rates: int


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
    early_stop: bool = False
    expected: Expected | None = None

    @property
    def total(self) -> float | None:
        if self.judge_cost is None or self.target_cost is None:
            return None
        return self.judge_cost + self.target_cost


# The attempts a scenario is expected to make before its gate is decided, when each attempt
# passes with probability `pass_rate`. A forward pass over the undecided (passes, fails)
# states: every unit of probability mass in an undecided state makes one more attempt, then
# moves to (passes + 1, fails) or (passes, fails + 1), and mass that reaches the count the
# gate needs, or one more failure than it tolerates, is decided and dropped. Attempts in
# flight when the decision lands still finish, so a real run makes a few more than this.
def expected_attempts(runs: int, misses: int, pass_rate: float) -> float:
    needed = runs - misses
    p = min(max(pass_rate, 0.0), 1.0)
    mass: dict[tuple[int, int], float] = {(0, 0): 1.0}
    expected = 0.0
    for _ in range(runs):
        following: dict[tuple[int, int], float] = {}
        for (passes, fails), probability in mass.items():
            expected += probability
            for state, chance in (((passes + 1, fails), p), ((passes, fails + 1), 1.0 - p)):
                if chance == 0.0 or state[0] >= needed or state[1] > misses:
                    continue
                following[state] = following.get(state, 0.0) + probability * chance
        mass = following
        if not mass:
            break
    return expected


def previous_pass_rates(previous: dict[str, Any] | None) -> dict[str, float]:
    rates: dict[str, float] = {}
    for criterion in (previous or {}).get("criteria", []):
        for entry in criterion.get("scenarios", []) if isinstance(criterion, dict) else []:
            judged, rate = entry.get("judged"), entry.get("pass_rate")
            if isinstance(judged, int) and judged > 0 and isinstance(rate, int | float):
                rates[str(entry.get("id"))] = float(rate)
    return rates


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
    rates = previous_pass_rates(previous)
    expected_attempts_total = 0.0
    expected_requests = 0.0
    with_rates = 0
    for scenario in scenarios:
        gate = config.run.gate(scenario.runs, scenario.misses, scenario.threshold)
        runs = gate.runs
        attempts += runs
        target_requests += runs * (len(scenario.turns) + 1)
        with_turns += 1 if scenario.turns else 0
        # Without a pass rate to go on, a scenario is planned in full.
        expected = float(runs)
        if scenario.id in rates:
            expected = expected_attempts(runs, gate.misses, rates[scenario.id])
            with_rates += 1
        expected_attempts_total += expected
        expected_requests += expected * (len(scenario.turns) + 1)
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
    expected_plan = None
    if config.run.early_stop_applies:
        # Both sides are priced per call, so the expected cost is the planned cost scaled
        # by the expected share of calls.
        expected_calls = expected_attempts_total * votes
        expected_plan = Expected(
            expected_attempts_total,
            expected_requests,
            expected_calls,
            scaled(judge_cost, expected_calls, judge_calls),
            scaled(target_cost, expected_requests, target_requests),
            with_rates,
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
        config.run.early_stop_applies,
        expected_plan,
    )


def scaled(cost: float | None, expected: float, planned: int) -> float | None:
    if cost is None:
        return None
    return cost * expected / planned if planned else 0.0


def describe_expected(plan: Plan, config: Config) -> str:
    if not plan.early_stop or plan.expected is None:
        return f"{config.run.describe_early_stop()}; every attempt above is planned"
    expected = plan.expected
    if not expected.with_rates:
        return (
            "early stop on, but no previous report supplies pass rates, so the expected "
            "saving is unknown and the figures above are the full plan"
        )
    text = (
        f"early stop: expected about {round(expected.attempts)} of {plan.attempts} attempts "
        f"({round(expected.target_requests)} target requests, {round(expected.judge_calls)} "
        f"judge calls) from the last report's pass rates for {expected.with_rates} of "
        f"{plan.scenarios} scenarios"
    )
    if expected.judge_cost is not None and expected.target_cost is not None:
        text += (
            f"; expected run cost {format_usd(expected.judge_cost + expected.target_cost)} "
            f"(judge {format_usd(expected.judge_cost)} + target {format_usd(expected.target_cost)})"
        )
    elif expected.judge_cost is not None:
        text += f"; expected judge cost {format_usd(expected.judge_cost)}, target unknown"
    return text


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
    lines.append(describe_expected(plan, config))
    if "assumed" in (plan.judge_tokens.source, plan.target_tokens.source):
        lines.append(
            "note: assumed token counts are a placeholder; a run reports the real figures and the "
            "next dry run uses its averages"
        )
    return lines
