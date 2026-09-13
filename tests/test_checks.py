import json
from pathlib import Path

import pytest

from juried.checks import (
    CheckError,
    contains,
    evaluate,
    failed,
    normalise,
    quoted_phrases,
    run_checks,
)
from juried.scenarios import Check, Scenario


def scenario(expected: str = "anything", **checks: object) -> Scenario:
    return Scenario(
        id="s",
        criterion="c",
        name="S",
        message="m",
        expected=expected,
        checks=[Check.model_validate({k: v}) for k, v in checks.items()],
    )


def test_normalise_ignores_case_spacing_hyphens_and_trailing_punctuation() -> None:
    assert normalise("  Fourteen-Days, please!  ") == "fourteen days please"
    assert normalise("14\tdays.\nFull refund") == "14 days full refund"
    assert normalise("help@example.com.") == "help@example.com"
    assert contains("You have 14-days, nothing more.", "14 days")
    assert contains("14  DAYS", "14 days.")
    assert not contains("fourteen days", "14 days")
    assert not contains("You have 14-days", "14 days", strict=True)
    assert contains("You have 14 Days", "14 days", strict=True)
    assert quoted_phrases('States the "14 days" window and a "full" refund') == ["14 days", "full"]


def test_each_check_kind(tmp_path: Path) -> None:
    root = tmp_path
    response = "You can return it within 14 days for a full refund."
    assert evaluate(Check(contains="14 days"), response, None, root, False).passed
    missing = evaluate(Check(contains="30 days"), response, None, root, False)
    assert not missing.passed and missing.reason == "response does not contain '30 days'"
    assert evaluate(Check(not_contains="30 days"), response, None, root, False).passed
    present = evaluate(Check(not_contains="14 days"), response, None, root, False)
    assert not present.passed and present.reason == "response contains '14 days'"
    assert evaluate(Check(regex=r"\b14[ -]?days?\b"), response, None, root, False).passed
    assert not evaluate(Check(regex=r"^\d+$"), response, None, root, False).passed
    assert evaluate(Check(max_latency_ms=500), response, 120.0, root, False).passed
    slow = evaluate(Check(max_latency_ms=100), response, 120.0, root, False)
    assert not slow.passed and slow.reason == "120 ms is over the limit"
    unmeasured = evaluate(Check(max_latency_ms=100), response, None, root, False)
    assert unmeasured.passed and unmeasured.reason == "latency not measured"
    assert evaluate(Check(max_chars=100), response, None, root, False).passed
    long = evaluate(Check(max_chars=10), response, None, root, False)
    assert not long.passed and long.reason == f"{len(response)} chars is over the limit"
    assert long.describe() == f"max_chars 10: {len(response)} chars is over the limit"
    assert long.to_dict() == {
        "kind": "max_chars",
        "value": 10,
        "source": "checks",
        "passed": False,
        "reason": f"{len(response)} chars is over the limit",
    }


def test_json_schema_check(tmp_path: Path) -> None:
    schema = tmp_path / "schemas" / "reply.json"
    schema.parent.mkdir()
    schema.write_text(
        json.dumps(
            {
                "type": "object",
                "properties": {"answer": {"type": "string"}, "days": {"type": "integer"}},
                "required": ["answer", "days"],
            }
        )
    )
    check = Check(json_schema="schemas/reply.json")
    good = evaluate(check, '{"answer": "yes", "days": 14}', None, tmp_path, False)
    assert good.passed and good.reason == "valid"
    bad = evaluate(check, '{"answer": "yes", "days": "fourteen"}', None, tmp_path, False)
    assert not bad.passed
    assert bad.reason.startswith("does not match reply.json at days: 'fourteen' is not of type")
    not_json = evaluate(check, "plain text", None, tmp_path, False)
    assert not not_json.passed and not_json.reason.startswith("response is not JSON")
    with pytest.raises(CheckError, match="cannot read"):
        evaluate(Check(json_schema="schemas/nope.json"), "{}", None, tmp_path, False)
    schema.write_text("{not json")
    with pytest.raises(CheckError, match="not valid JSON"):
        evaluate(check, "{}", None, tmp_path, False)
    schema.write_text(json.dumps({"type": "nonsense"}))
    with pytest.raises(CheckError, match="not a valid schema"):
        evaluate(check, "{}", None, tmp_path, False)


def test_run_checks_puts_quoted_phrases_first_and_keeps_every_outcome(tmp_path: Path) -> None:
    s = scenario('Says "14 days" and "full refund".', not_contains="30 days", max_chars=20)
    outcomes = run_checks(s, "Within 14-days you get a full refund; not 30 days.", 50.0, tmp_path)
    assert [(o.check.kind, o.source, o.passed) for o in outcomes] == [
        ("contains", "expected", True),
        ("contains", "expected", True),
        ("not_contains", "checks", False),
        ("max_chars", "checks", False),
    ]
    assert [o.check.describe() for o in failed(outcomes)] == [
        "not_contains '30 days'",
        "max_chars 20",
    ]
    # strict_quotes applies to the phrases from `expected` only.
    strict = run_checks(s, "Within 14-days, full refund.", None, tmp_path, strict_quotes=True)
    assert [o.passed for o in strict] == [False, True, True, False]
    lenient = run_checks(s, "Within 14-days, full refund.", None, tmp_path)
    assert [o.passed for o in lenient] == [True, True, True, False]
    assert run_checks(scenario("no quotes here"), "anything", None, tmp_path) == []
