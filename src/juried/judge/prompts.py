from __future__ import annotations

import json
from typing import Any

from juried.criteria import Criterion
from juried.scenarios import Scenario, Turn

PROMPT_VERSION = "1"

JUDGE_SYSTEM = """You are the judge in an acceptance test suite for a software product feature \
that uses a language model. You are given one acceptance criterion, one test scenario written \
by a QA engineer, and the actual response the feature produced. Decide whether the response \
satisfies the criterion in the way the scenario expects.

Rules:
- Judge only what is asked. Do not penalise style, length or tone unless the expectation \
mentions them.
- A response that ignores the request, contradicts the expectation, invents facts the expectation \
rules out, or refuses without reason fails.
- If the expectation is met in substance, pass, even if the wording differs.
- Be strict about factual details named in the expectation (numbers, names, policies).

Respond with JSON only: {"pass": true or false, "reason": "one short sentence"}."""

JUDGE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "pass": {"type": "boolean"},
        "reason": {"type": "string"},
    },
    "required": ["pass", "reason"],
    "additionalProperties": False,
}

GENERATE_SYSTEM = """You write acceptance test scenarios for a QA team testing a product feature \
that uses a language model. The feature is a black box that takes a user message and returns text. \
Given one acceptance criterion, produce a set of single turn scenarios: realistic user messages a \
tester would send, each with a plain English description of what a passing response must contain \
or do.

Rules:
- Produce a mix of happy path scenarios and edge cases (ambiguous wording, typos, terse messages, \
out of scope requests, attempts to get the feature to contradict the criterion).
- Each message must be something a real user would type. No placeholders.
- Each expectation must be checkable by reading the response alone, and must be specific enough \
that two reviewers would agree on it.
- Names are short, unique and descriptive, written in sentence case.
- Use UK English.

Respond with JSON only: {"scenarios": [{"name": ..., "kind": "happy_path" or "edge_case", \
"message": ..., "expected": ...}]}."""

GENERATE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "scenarios": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "kind": {"type": "string", "enum": ["happy_path", "edge_case"]},
                    "message": {"type": "string"},
                    "expected": {"type": "string"},
                },
                "required": ["name", "kind", "message", "expected"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["scenarios"],
    "additionalProperties": False,
}


def format_history(history: list[Turn]) -> str:
    if not history:
        return "(none)"
    return "\n".join(f"{turn.role}: {turn.content}" for turn in history)


def judge_user_prompt(criterion: Criterion, scenario: Scenario, response_text: str) -> str:
    return (
        f"Acceptance criterion: {criterion.title}\n{criterion.description}\n\n"
        f"Scenario: {scenario.name}\n"
        f"Conversation so far:\n{format_history(scenario.history)}\n\n"
        f"User message:\n{scenario.message}\n\n"
        f"Expected of a passing response:\n{scenario.expected}\n\n"
        f"Actual response:\n{response_text}\n\n"
        "Does the actual response meet the expectation? Answer with the JSON object."
    )


def generate_user_prompt(criterion: Criterion, count: int) -> str:
    return (
        f"Acceptance criterion: {criterion.title}\n{criterion.description}\n\n"
        f"Write {count} scenarios, roughly half happy path and half edge cases.\n"
        f"Answer with the JSON object described. Schema: {json.dumps(GENERATE_SCHEMA)}"
    )
