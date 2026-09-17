import json
import math
from pathlib import Path

import pytest

from juried.cli import main
from juried.config import parse_config
from juried.estimate import expected_attempts, plan_run, previous_pass_rates
from juried.scenarios import Scenario

ACCEPTANCE = "# Acceptance criteria\n\n## Opening hours\nThe bot states the hours.\n"


def closed_form(runs: int, misses: int, p: float) -> float:
    # Every undecided state (a passes, b fails) is reached with probability
    # C(a + b, a) p^a (1 - p)^b and makes exactly one more attempt.
    return sum(
        math.comb(a + b, a) * p**a * (1 - p) ** b
        for a in range(runs - misses)
        for b in range(misses + 1)
    )


def test_expected_attempts_matches_hand_computed_cases() -> None:
    # 3 runs, 1 miss, p = 0.5: (0,0) 1, then (1,0) and (0,1) at 0.5 each, then (1,1) at
    # 0.5, the rest decided: 1 + 1 + 0.5 = 2.5.
    assert expected_attempts(3, 1, 0.5) == pytest.approx(2.5)
    # 4 runs, 1 miss, p = 0.5: 1 + 0.5 + 0.5 + 0.5 + 0.25 + 0.375 = 3.125.
    assert expected_attempts(4, 1, 0.5) == pytest.approx(3.125)
    # A scenario that always passes is decided at the count the gate needs, and one that
    # always fails at one more failure than it tolerates.
    assert expected_attempts(20, 1, 1.0) == pytest.approx(19.0)
    assert expected_attempts(20, 1, 0.0) == pytest.approx(2.0)
    assert expected_attempts(20, 3, 0.0) == pytest.approx(4.0)
    for runs, misses, p in ((20, 1, 0.9), (20, 3, 0.95), (10, 4, 0.5), (50, 8, 0.8)):
        assert expected_attempts(runs, misses, p) == pytest.approx(closed_form(runs, misses, p))
        assert expected_attempts(runs, misses, p) <= runs
    # A healthy scenario saves little; a failing one is decided in a few attempts.
    assert 17.5 < expected_attempts(20, 1, 0.95) < 18
    assert 3.5 < expected_attempts(20, 1, 0.5) < 4.5


def test_previous_pass_rates_reads_judged_scenarios_only() -> None:
    previous = {
        "criteria": [
            {
                "scenarios": [
                    {"id": "a", "judged": 10, "pass_rate": 0.5},
                    {"id": "b", "judged": 0, "pass_rate": 0.0},
                    {"id": "c", "pass_rate": 0.9},
                ]
            }
        ]
    }
    assert previous_pass_rates(previous) == {"a": 0.5}
    assert previous_pass_rates(None) == {}


def test_plan_expects_fewer_attempts_under_early_stopping(tmp_path: Path) -> None:
    config = parse_config(
        '[target]\nurl = "http://127.0.0.1:9/chat"\ncost_per_request = 0.01\n'
        '[run]\nruns = 4\nmisses = 1\n[judge]\nprovider = "stub"\n',
        tmp_path,
        environ={},
    )
    known = Scenario(id="known", criterion="opening-hours", name="Known", message="m", expected="e")
    fresh = Scenario(id="fresh", criterion="opening-hours", name="Fresh", message="m", expected="e")
    previous = {"criteria": [{"scenarios": [{"id": "known", "judged": 10, "pass_rate": 0.5}]}]}
    plan = plan_run(config, [known, fresh], previous)
    assert plan.attempts == 8 and plan.early_stop
    assert plan.expected is not None
    assert plan.expected.attempts == pytest.approx(3.125 + 4)
    assert plan.expected.target_requests == pytest.approx(7.125)
    assert plan.expected.judge_calls == pytest.approx(7.125)
    assert plan.expected.with_rates == 1
    assert plan.expected.target_cost == pytest.approx(0.07125)
    assert plan.expected.judge_cost == 0.0
    config.run.early_stop = False
    assert plan_run(config, [known, fresh], previous).expected is None


def test_dry_run_reports_expected_cost_under_early_stopping(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "juried.toml").write_text(
        '[target]\nurl = "http://127.0.0.1:9/chat"\ncost_per_request = 0.01\n'
        '[run]\nruns = 4\nmisses = 1\n[judge]\nprovider = "stub"\n'
    )
    (tmp_path / "acceptance.md").write_text(ACCEPTANCE)
    scenarios = tmp_path / "scenarios"
    scenarios.mkdir()
    (scenarios / "hours.yaml").write_text(
        "criterion: opening-hours\nscenarios:\n"
        '  - name: Plain\n    message: hours?\n    expected: Mentions "9am".\n'
    )
    monkeypatch.chdir(tmp_path)
    assert main(["run", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "1 scenario under scenarios, 4 attempts in all" in out
    assert (
        "early stop on, but no previous report supplies pass rates, so the expected saving "
        "is unknown and the figures above are the full plan"
    ) in out
    (tmp_path / "reports").mkdir()
    (tmp_path / "reports" / "juried-report.json").write_text(
        json.dumps(
            {
                "tool": "juried",
                "criteria": [
                    {"scenarios": [{"id": "opening-hours-plain", "judged": 10, "pass_rate": 0.5}]}
                ],
            }
        )
    )
    assert main(["estimate"]) == 0
    out = capsys.readouterr().out
    assert "estimated run cost: $0.0400 (judge $0.0000 + target $0.0400)" in out
    assert (
        "early stop: expected about 3 of 4 attempts (3 target requests, 3 judge calls) from "
        "the last report's pass rates for 1 of 1 scenarios; expected run cost $0.0312 "
        "(judge $0.0000 + target $0.0312)"
    ) in out
    assert main(["estimate", "--no-early-stop"]) == 0
    out = capsys.readouterr().out
    assert "early stop off (early_stop = false); every attempt above is planned" in out
    assert "expected about" not in out
    assert main(["run", "--dry-run", "--no-early-stop"]) == 0
    assert "every attempt above is planned" in capsys.readouterr().out


def test_run_passes_no_early_stop_to_pytest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    (tmp_path / "juried.toml").write_text('[target]\nurl = "http://127.0.0.1:9/chat"\n')
    (tmp_path / "acceptance.md").write_text(ACCEPTANCE)
    (tmp_path / "scenarios").mkdir()
    monkeypatch.chdir(tmp_path)
    seen: list[list[str]] = []

    def fake_main(args: list[str]) -> int:
        seen.append(list(args))
        return 0

    monkeypatch.setattr("juried.cli.pytest.main", fake_main)
    assert main(["run", "--no-early-stop", "-k", "hours"]) == 0
    assert "--juried-no-early-stop" in seen[0] and "-k" in seen[0]
    assert main(["run"]) == 0
    assert "--juried-no-early-stop" not in seen[1]
