from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from juried.scenarios import Check, Scenario

QUOTED = re.compile(r'"([^"]+)"')
# Punctuation that ends a word is dropped, so "14 days." matches "14 days" and vice versa.
TRAILING_PUNCTUATION = re.compile(r"[.,;:!?'\"()\[\]]+(?=\s|$)")


class CheckError(Exception):
    """A check that cannot run at all: a missing schema file, a package not installed."""


# One rule for "contains" and for quoted phrases in `expected`: case does not matter,
# any run of whitespace is one space, a hyphen is a space, and punctuation ending a word
# is ignored. Wording still has to match; this is not a paraphrase test.
def normalise(text: str) -> str:
    text = TRAILING_PUNCTUATION.sub("", text.lower().replace("-", " "))
    return " ".join(text.split())


def contains(haystack: str, needle: str, strict: bool = False) -> bool:
    if strict:
        return needle.lower() in haystack.lower()
    return normalise(needle) in normalise(haystack)


def quoted_phrases(expected: str) -> list[str]:
    return QUOTED.findall(expected)


@dataclass(frozen=True)
class CheckOutcome:
    check: Check
    passed: bool
    reason: str
    # "expected" for a quoted phrase in the expectation, "checks" for the scenario's list.
    source: str = "checks"

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.check.kind,
            "value": self.check.value,
            "source": self.source,
            "passed": self.passed,
            "reason": self.reason,
        }

    def describe(self) -> str:
        return f"{self.check.describe()}: {self.reason}"


def load_schema(path: Path) -> dict[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CheckError(f"json_schema {path}: cannot read: {exc}") from exc
    except ValueError as exc:
        raise CheckError(f"json_schema {path}: not valid JSON: {exc}") from exc
    if not isinstance(schema, dict):
        raise CheckError(f"json_schema {path}: expected an object at the top level")
    return schema


def validate_json(response: str, schema_path: Path) -> str | None:
    try:
        import jsonschema
    except ImportError as exc:
        raise CheckError(
            "json_schema checks need the jsonschema package: pip install 'juried[schema]'"
        ) from exc
    schema = load_schema(schema_path)
    try:
        data = json.loads(response)
    except ValueError as exc:
        return f"response is not JSON: {exc}"
    try:
        jsonschema.validate(data, schema)
    except jsonschema.SchemaError as exc:
        raise CheckError(f"json_schema {schema_path}: not a valid schema: {exc.message}") from exc
    except jsonschema.ValidationError as exc:
        where = "/".join(str(part) for part in exc.absolute_path) or "root"
        return f"does not match {schema_path.name} at {where}: {exc.message}"
    return None


def evaluate(
    check: Check,
    response: str,
    latency_ms: float | None,
    root: Path,
    strict_quotes: bool,
    source: str = "checks",
) -> CheckOutcome:
    kind, value = check.kind, check.value
    if kind == "contains":
        found = contains(response, str(value), strict_quotes and source == "expected")
        return CheckOutcome(
            check, found, "found" if found else f"response does not contain {value!r}", source
        )
    if kind == "not_contains":
        found = contains(response, str(value))
        return CheckOutcome(
            check, not found, f"response contains {value!r}" if found else "absent", source
        )
    if kind == "regex":
        matched = re.search(str(value), response) is not None
        return CheckOutcome(check, matched, "matched" if matched else "no match", source)
    if kind == "json_schema":
        path = root / str(value)
        problem = validate_json(response, path)
        return CheckOutcome(check, problem is None, problem or "valid", source)
    if kind == "max_latency_ms":
        if latency_ms is None:
            return CheckOutcome(check, True, "latency not measured", source)
        slow = latency_ms > float(value)
        return CheckOutcome(
            check, not slow, f"{latency_ms:.0f} ms" + (" is over the limit" if slow else ""), source
        )
    if kind == "max_chars":
        long = len(response) > int(value)
        return CheckOutcome(
            check,
            not long,
            f"{len(response)} chars" + (" is over the limit" if long else ""),
            source,
        )
    raise CheckError(f"unknown check {kind!r}")


# Quoted phrases in `expected` run first as implicit contains checks, then the scenario's
# own list, in order; every outcome is kept so the report can show what ran.
def run_checks(
    scenario: Scenario,
    response: str,
    latency_ms: float | None,
    root: Path,
    strict_quotes: bool = False,
) -> list[CheckOutcome]:
    outcomes = [
        evaluate(Check(contains=phrase), response, latency_ms, root, strict_quotes, "expected")
        for phrase in quoted_phrases(scenario.expected)
    ]
    outcomes.extend(
        evaluate(check, response, latency_ms, root, strict_quotes) for check in scenario.checks
    )
    return outcomes


def failed(outcomes: Sequence[CheckOutcome]) -> list[CheckOutcome]:
    return [outcome for outcome in outcomes if not outcome.passed]
