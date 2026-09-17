import json
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import pytest

ACCEPTANCE = """# Acceptance criteria

## Opening hours
The bot states the opening hours, 9am to 5pm.

## Refund policy
Refunds are accepted within 14 days.
"""

HOURS_SCENARIOS = """
criterion: opening-hours
scenarios:
  - name: Asks hours
    kind: happy_path
    message: What are your opening hours?
    expected: Gives the hours including "9am" and "5pm".
"""

NONSENSE_SCENARIOS = """
criterion: refund-policy
scenarios:
  - name: Asks nonsense
    kind: edge_case
    message: blorp
    expected: Mentions "14 days".
"""


def write_project(pytester: pytest.Pytester, url: str, runs: int = 4, extra: str = "") -> None:
    pytester.makefile(
        ".toml",
        juried=f"""
[target]
url = "{url}/chat"
retries = 0

[run]
runs = {runs}
misses = 0

[judge]
provider = "stub"
{extra}
""",
    )
    pytester.makefile(".md", acceptance=ACCEPTANCE)
    scenarios = pytester.mkdir("scenarios")
    (scenarios / "b-hours.yaml").write_text(HOURS_SCENARIOS)
    (scenarios / "a-nonsense.yaml").write_text(NONSENSE_SCENARIOS)


def test_collects_and_reports(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines(
        [
            r"juried: config .*juried.toml, judge stub/stub, runs 4, misses 0, "
            r"cache verdicts only, concurrency 4 target / 4 judge",
            r"juried: gate needs 4/4 passes \(no misses tolerated\)",
            r".*a-nonsense.yaml::refund-policy-asks-nonsense FAILED 0/4 \(needs 4, "
            r"interval 0.00 to 0.49\), 4 failed a check.*",
            r".*b-hours.yaml::opening-hours-asks-hours PASSED 4/4 \(needs 4, "
            r"interval 0.51 to 1.00\).*",
            r"juried gate failed for scenario 'refund-policy-asks-nonsense' \(Asks nonsense\)",
            r"\s+criterion: refund-policy \(Refund policy\)",
            r"\s+runs upheld: 0/4 judged = 0.00",
            r"\s+gate: gate needs 4/4 passes \(no misses tolerated\); 4 misses on the judged "
            r"attempts, so the gate is not met",
            r"\s+interval: Wilson 95% interval 0.00 to 0.49",
            r"\s+transport errors: 0",
            r"\s+judge errors: 0",
            r"\s+first failing run: attempt 1 \(failed\)",
            r"\s+user: blorp",
            r"\s+response: I'm not sure about that, please contact support.",
            r"\s+checks: 0 of 1 passed",
            r"\s+failed contains '14 days': response does not contain '14 days'",
            r"\s+judge: not called, a check decided the attempt",
            r"2 scenarios, 1 upheld, 1 failed, 0 incomplete, 0 transport errors, 0 judge errors",
            r"judge usage: 0 input \+ 0 output tokens over 0 call\(s\), estimated \$0.0000 at "
            r"list prices of 20",
            r"target usage: 8 requests, cost unknown \(set \[target\] input_price/output_price "
            r"or cost_per_request\)",
            r"report: .*juried-report.html",
        ]
    )
    assert (pytester.path / "reports" / "juried-report.html").is_file()
    assert (pytester.path / "reports" / "juried-report.json").is_file()
    assert (pytester.path / ".juried" / "verdicts.jsonl").is_file()
    assert (pytester.path / ".juried" / "cache" / "verdicts").is_dir()
    assert not (pytester.path / ".juried" / "cache" / "responses").exists()
    assert "replayed from" not in result.stdout.str()
    assert "threshold" not in result.stdout.str()
    assert "estimated run cost" not in result.stdout.str()
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    assert report["summary"]["usage"]["target"]["requests"] == 8
    assert report["summary"]["usage"]["total_estimate_usd"] is None


def test_target_usage_is_priced_per_request_or_by_tokens(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    text = (pytester.path / "juried.toml").read_text()
    (pytester.path / "juried.toml").write_text(
        text.replace("retries = 0", "retries = 0\ncost_per_request = 0.01")
    )
    flat = pytester.runpytest("-k", "hours")
    flat.assert_outcomes(passed=1)
    flat.stdout.fnmatch_lines(
        [
            "target usage: 4 requests, estimated $0.0400 at $0.0100 each",
            "estimated run cost: $0.0400 (judge $0.0000 + target $0.0400)",
        ]
    )
    # The fake bot echoes the request under "echo", which serves as a token count here.
    (pytester.path / "juried.toml").write_text(
        text.replace(
            "retries = 0",
            'retries = 0\nusage_input_path = "echo.tokens"\nusage_output_path = "echo.tokens"\n'
            "input_price = 1\noutput_price = 1",
        )
    )
    (pytester.path / "juried.toml").write_text(
        (pytester.path / "juried.toml")
        .read_text()
        .replace("[run]", '[target.body]\nmessage = "{{message}}"\ntokens = 250\n\n[run]')
    )
    tokens = pytester.runpytest("-k", "hours")
    tokens.assert_outcomes(passed=1)
    tokens.stdout.fnmatch_lines(
        [
            "target usage: 4 requests, 1,000 input + 1,000 output tokens, estimated $0.0020 at "
            "configured prices",
            "estimated run cost: $0.0020 (judge $0.0000 + target $0.0020)",
        ]
    )
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    assert report["summary"]["usage"]["target"]["input_tokens"] == 1000
    assert report["summary"]["usage"]["total_estimate_usd"] == pytest.approx(0.002)


def test_keyword_and_marker_selection(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    pytester.runpytest("-k", "hours").assert_outcomes(passed=1)
    pytester.runpytest("-m", "edge_case").assert_outcomes(failed=1)
    pytester.runpytest("-m", "happy_path").assert_outcomes(passed=1)
    result = pytester.runpytest("--collect-only", "-q")
    result.stdout.fnmatch_lines(
        [
            "scenarios/a-nonsense.yaml::refund-policy-asks-nonsense",
            "scenarios/b-hours.yaml::opening-hours-asks-hours",
        ]
    )


def test_live_turns_are_shown_for_failing_runs(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    (pytester.path / "scenarios" / "c-turns.yaml").write_text(
        """
criterion: opening-hours
scenarios:
  - name: Asks twice
    turns:
      - what are your hours?
    message: and the history?
    expected: Mentions "History had 2 turns" and "9am".
"""
    )
    result = pytester.runpytest("-v", "-k", "twice")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*user (live): what are your hours?",
            "*assistant (live): We are open Monday to Friday, 9am to 5pm.",
            "*user: and the history?",
            "*response: History had 2 turns.",
            "*checks: 1 of 2 passed",
            "*failed contains '9am': response does not contain '9am'",
        ]
    )
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    scenario = report["criteria"][0]["scenarios"][0]
    assert scenario["turns"] == ["what are your hours?"]
    assert scenario["failures"][0]["transcript"][1]["content"].startswith("We are open")
    html = (pytester.path / "reports" / "juried-report.html").read_text()
    assert "user (live)</span>what are your hours?" in html
    assert "live reply, shown per run below" in html


def test_failed_checks_are_shown_and_skip_the_judge(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    (pytester.path / "scenarios" / "c-checks.yaml").write_text(
        """
criterion: opening-hours
scenarios:
  - name: Checked
    message: What are your opening hours?
    expected: Gives the "9am" opening time.
    checks:
      - not_contains: "5pm"
      - max_chars: 500
      - regex: "\\\\b9am\\\\b"
"""
    )
    result = pytester.runpytest("-v", "-k", "checked")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*checked FAILED 0/4 (needs 4, interval 0.00 to 0.49), 4 failed a check*",
            "*checks: 3 of 4 passed",
            "*failed not_contains '5pm': response contains '5pm'",
            "*judge: not called, a check decided the attempt",
        ]
    )
    assert "judge (stub)" not in result.stdout.str()
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    scenario = report["criteria"][0]["scenarios"][0]
    assert scenario["checks_failed"] == 4
    assert scenario["failures"][0]["by_checks"] is True
    assert [c["kind"] for c in scenario["failures"][0]["checks"]] == [
        "contains",
        "not_contains",
        "max_chars",
        "regex",
    ]
    html = (pytester.path / "reports" / "juried-report.html").read_text()
    assert "not_contains 5pm: response contains &#39;5pm&#39;" in html
    assert "not called, a check decided the attempt" in html
    assert "4 by checks" in html
    assert '<p><span class="label">checks</span>not_contains 5pm; max_chars 500; regex' in html


def test_early_stop_is_reported_everywhere(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url, runs=20)
    result = pytester.runpytest("-v", "--junitxml=out.xml")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(
        [
            "juried: early stop on (a scenario ends once its gate is decided)",
            "*FAILED 0/* (needs 20, interval *stopped after * of 20*",
            "*PASSED 20/20 (needs 20, interval *",
        ]
    )
    result.stdout.re_match_lines([r"early stop saved \d+ of 40 planned attempts"])
    result.stdout.fnmatch_lines(
        ["  attempts: stopped after * of 20 planned (the gate was decided, so the attempts *"]
    )
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    assert report["defaults"]["early_stop"] is True
    summary = report["summary"]
    assert summary["early_stopped"] is True and summary["scenarios_stopped"] == 1
    assert summary["attempts_planned"] == 40
    assert summary["attempts_made"] < 40
    scenarios = {s["id"]: s for c in report["criteria"] for s in c["scenarios"]}
    stopped = scenarios["refund-policy-asks-nonsense"]
    assert stopped["early_stopped"] is True
    assert stopped["attempts_planned"] == 20
    assert 1 <= stopped["attempts_made"] < 20
    assert stopped["runs"] == stopped["attempts_made"] == len(stopped["attempts"])
    assert stopped["required_passes"] == 20
    full = scenarios["opening-hours-asks-hours"]
    assert full["early_stopped"] is False and full["attempts_made"] == 20
    html = (pytester.path / "reports" / "juried-report.html").read_text()
    assert f"stopped after {stopped['attempts_made']} of 20" in html
    assert "Early stop on: a scenario ends once its gate is decided, which saved" in html
    assert "a bound rather than an estimate" in html
    assert "20 / 20<br>" in html
    root = ET.parse(pytester.path / "out.xml").getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    assert suite is not None
    cases = {case.get("name"): case for case in suite.findall("testcase")}
    properties = {
        p.get("name"): p.get("value") for p in cases["refund-policy-asks-nonsense"].iter("property")
    }
    assert properties["early_stopped"] == "true"
    assert properties["attempts_planned"] == "20"
    assert properties["attempts_made"] == str(stopped["attempts_made"])
    assert properties["runs"] == str(stopped["attempts_made"])
    full_properties = {
        p.get("name"): p.get("value") for p in cases["opening-hours-asks-hours"].iter("property")
    }
    assert full_properties["early_stopped"] == "false"
    assert full_properties["attempts_made"] == "20"


def test_early_stop_can_be_switched_off_by_flag_or_config(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url, runs=8)
    result = pytester.runpytest("-v", "--juried-no-early-stop")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.fnmatch_lines(["juried: early stop off (early_stop = false)"])
    assert "early stop saved" not in result.stdout.str()
    assert "stopped after" not in result.stdout.str()
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    assert report["defaults"]["early_stop"] is False
    assert report["summary"]["attempts_made"] == 16
    assert report["summary"]["early_stopped"] is False
    assert "Early stop off: every planned attempt was made." in (
        (pytester.path / "reports" / "juried-report.html").read_text()
    )
    text = (pytester.path / "juried.toml").read_text()
    (pytester.path / "juried.toml").write_text(
        text.replace("misses = 0", "misses = 0\nearly_stop = false")
    )
    again = pytester.runpytest("-v")
    again.assert_outcomes(passed=1, failed=1)
    again.stdout.fnmatch_lines(["juried: early stop off (early_stop = false)"])


def test_gate_on_corrected_switches_early_stop_off(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    text = (pytester.path / "juried.toml").read_text()
    (pytester.path / "juried.toml").write_text(
        text.replace("misses = 0", 'misses = 0\ngate_on = "corrected"')
    )
    write_calibration(pytester, CALIBRATION_REPORT_STUB)
    result = pytester.runpytest("-v", "-k", "hours")
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        ["juried: early stop off (gate_on = corrected needs full sampling)"]
    )
    assert "early stop saved" not in result.stdout.str()
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    assert report["defaults"]["early_stop"] is False


def test_early_stop_and_x_cancellation(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    # The failing scenario is decided after its first attempt and -x then cancels the
    # passing one, which is still in flight.
    write_project(pytester, fake_bot_url, runs=50)
    result = pytester.runpytest("-x", "-v")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*stopped after * of 50*", "*stopping after 1 failures*"])
    assert "Task was destroyed" not in result.stderr.str()


def test_stops_on_first_failure(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    result = pytester.runpytest("-x")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*stopping after 1 failures*"])
    assert "Task was destroyed" not in result.stderr.str()


def test_scenarios_run_together_and_report_in_order(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    pytester.makeconftest(
        """
import pytest
from juried.pytest_plugin import ScenarioItem

@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item):
    if isinstance(item, ScenarioItem):
        assert item.future is not None
        # Every scenario has already been submitted by the time the first item runs.
        assert all(
            other.future is not None
            for other in item.session.items
            if isinstance(other, ScenarioItem)
        )
"""
    )
    result = pytester.runpytest("-v", "-p", "no:cacheprovider")
    result.assert_outcomes(passed=1, failed=1)
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    ids = [s["id"] for c in report["criteria"] for s in c["scenarios"]]
    assert ids == ["opening-hours-asks-hours", "refund-policy-asks-nonsense"]


def test_junit_xml(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    pytester.runpytest("--junitxml=out.xml").assert_outcomes(passed=1, failed=1)
    root = ET.parse(pytester.path / "out.xml").getroot()
    suite = root if root.tag == "testsuite" else root.find("testsuite")
    assert suite is not None
    assert suite.get("tests") == "2"
    assert suite.get("failures") == "1"
    cases = {case.get("name"): case for case in suite.findall("testcase")}
    assert set(cases) == {"refund-policy-asks-nonsense", "opening-hours-asks-hours"}
    failure = cases["refund-policy-asks-nonsense"].find("failure")
    assert failure is not None
    assert "runs upheld: 0/4 judged" in (failure.get("message") or "") + (failure.text or "")
    properties = {
        p.get("name"): p.get("value") for p in cases["opening-hours-asks-hours"].iter("property")
    }
    assert properties["passes"] == "4"
    assert properties["interval_lower"] == "0.5101"
    assert properties["misses_tolerated"] == "0"
    assert properties["passes_needed"] == "4"
    assert properties["threshold"] == "0.5101"
    assert properties["criterion"] == "opening-hours"
    assert cases["opening-hours-asks-hours"].find("failure") is None


def test_overrides_and_cache_flag(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    result = pytester.runpytest("--juried-runs=2", "--juried-misses=1", "--juried-no-cache", "-v")
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines(
        [
            r"juried: config .*, runs 2, misses 1, cache off",
            r"juried: gate needs 1/2 passes \(1 miss tolerated\)",
        ]
    )
    assert not (pytester.path / ".juried" / "cache").exists()


CALIBRATION_REPORT_STUB: dict[str, Any] = {
    "tool": "juried",
    "generated_at": "2026-09-12T10:00:00+00:00",
    "judge": {"provider": "stub", "model": "stub", "votes": 1},
    "summary": {"accuracy": 0.9},
    # 24 human passes of which the stub passed 23, 16 human fails of which it failed 14.
    "cases": [{"criterion": "opening-hours", "human": "pass", "judge": "pass"}] * 23
    + [{"criterion": "opening-hours", "human": "pass", "judge": "fail"}]
    + [{"criterion": "opening-hours", "human": "fail", "judge": "fail"}] * 14
    + [{"criterion": "opening-hours", "human": "fail", "judge": "pass"}] * 2,
}


def write_calibration(pytester: pytest.Pytester, report: dict[str, Any]) -> None:
    (pytester.path / "reports").mkdir(exist_ok=True)
    (pytester.path / "reports" / "juried-calibration.json").write_text(json.dumps(report))


def test_corrected_rate_is_reported_when_a_calibration_report_matches(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    write_calibration(pytester, CALIBRATION_REPORT_STUB)
    result = pytester.runpytest("-v", "-k", "hours")
    result.assert_outcomes(passed=1)
    # Observed 4/4 = 100%; sensitivity 23/24, specificity 14/16: (1 + 0.875 - 1) / 0.833 = 100%.
    result.stdout.fnmatch_lines(
        [
            "*PASSED 4/4 (needs 4, interval 0.51 to 1.00); corrected 100% (* to 100%), judge "
            "false pass 12%, false fail 4%*",
            "calibration: corrected rates use juried-calibration.json (stub/stub, 40 cases, "
            "2026-09-12)",
        ]
    )
    assert "no corrected rate" not in result.stdout.str()
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    scenario = report["criteria"][0]["scenarios"][0]
    assert scenario["corrected_rate"] == 1.0
    assert scenario["judge_sensitivity"] == pytest.approx(23 / 24, abs=1e-4)
    assert scenario["calibration_cases_used"] == 40
    assert scenario["calibration_scope"] == "criterion"
    assert scenario["bootstrap_seed"] == 20260913
    assert scenario["gate_on"] == "observed"
    assert report["calibration"]["model"] == "stub"
    assert report["calibration"]["cases"] == 40
    html = (pytester.path / "reports" / "juried-report.html").read_text()
    assert "Corrected rates use <code>" in html
    assert '<th class="num">Corrected</th>' in html
    assert '100%<br><span class="meta">' in html
    junit = pytester.runpytest("-k", "hours", "--junitxml=out.xml")
    junit.assert_outcomes(passed=1)
    assert 'name="corrected_rate" value="1.0000"' in (pytester.path / "out.xml").read_text()
    assert 'name="bootstrap_seed" value="20260913"' in (pytester.path / "out.xml").read_text()


def test_gate_on_corrected_uses_the_corrected_lower_bound(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    text = (pytester.path / "juried.toml").read_text()
    (pytester.path / "juried.toml").write_text(
        text.replace("misses = 0", 'misses = 0\ngate_on = "corrected"')
    )
    missing = pytester.runpytest("-k", "hours")
    assert missing.ret == pytest.ExitCode.USAGE_ERROR
    missing.stderr.fnmatch_lines(
        ['*gate_on = "corrected" needs a calibration report for this judge*']
    )
    write_calibration(pytester, CALIBRATION_REPORT_STUB)
    result = pytester.runpytest("-v", "-k", "hours")
    result.stdout.re_match_lines(
        [
            r"juried: gate on judge-corrected rate \(the corrected interval's lower bound must "
            r"meet 100%\)"
        ]
    )
    # 4/4 observed corrects to 100% whatever the judge's rates, so this gate holds; the
    # failing direction is covered on the runner with 18 of 20.
    result.assert_outcomes(passed=1)
    result.stdout.fnmatch_lines(
        ["*PASSED 4/4 (needs 4, interval 0.51 to 1.00); corrected 100% (100% to 100%)*"]
    )
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    scenario = report["criteria"][0]["scenarios"][0]
    assert scenario["gate_on"] == "corrected" and scenario["status"] == "upheld"
    assert (
        "gated on the judge-corrected rate"
        in (pytester.path / "reports" / "juried-report.html").read_text()
    )
    # A judge too weak to correct falls back to the observed gate and says so.
    weak = dict(CALIBRATION_REPORT_STUB)
    weak["cases"] = (
        [{"criterion": "opening-hours", "human": "pass", "judge": "pass"}] * 7
        + [{"criterion": "opening-hours", "human": "pass", "judge": "fail"}] * 3
        + [{"criterion": "opening-hours", "human": "fail", "judge": "fail"}] * 7
        + [{"criterion": "opening-hours", "human": "fail", "judge": "pass"}] * 3
    )
    write_calibration(pytester, weak)
    fallback = pytester.runpytest("-v", "-k", "hours")
    fallback.assert_outcomes(passed=1)
    fallback.stdout.fnmatch_lines(
        ["*judge too weak to correct (sensitivity 0.70 + specificity 0.70*"]
    )


def test_mismatched_calibration_report_is_warned_about(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    write_calibration(
        pytester, {**CALIBRATION_REPORT_STUB, "judge": {"provider": "openai", "model": "gpt-4.1"}}
    )
    result = pytester.runpytest("-k", "hours")
    result.assert_outcomes(passed=1)
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    assert report["calibration"] is None
    assert report["criteria"][0]["scenarios"][0]["corrected_rate"] is None
    html = (pytester.path / "reports" / "juried-report.html").read_text()
    assert "No calibration report matched this judge" in html


def test_xdist_workers_share_the_concurrency_caps(
    pytester: pytest.Pytester, fake_bot_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from juried.pytest_plugin import Concurrency, xdist_workers

    assert xdist_workers({}) is None
    assert xdist_workers({"PYTEST_XDIST_WORKER_COUNT": "4"}) == 4
    assert xdist_workers({"PYTEST_XDIST_WORKER_COUNT": "0"}) is None
    assert xdist_workers({"PYTEST_XDIST_WORKER_COUNT": "many"}) is None
    assert Concurrency(4, 4, 4).per_worker("global") == Concurrency(1, 1, 4)
    assert Concurrency(4, 2, 3).per_worker("global") == Concurrency(1, 1, 3)
    assert Concurrency(10, 6, 4).per_worker("global") == Concurrency(2, 1, 4)
    assert Concurrency(4, 4, 4).per_worker("worker") == Concurrency(4, 4, 4)
    assert Concurrency(4, 4).per_worker("global") == Concurrency(4, 4)

    write_project(pytester, fake_bot_url, extra="concurrency = 6")
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "4")
    result = pytester.runpytest("-k", "hours")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines(
        [
            r"juried: config .*concurrency 4 target / 6 judge \(1 target / 1 judge per worker, "
            r"4 xdist workers, scope global\)"
        ]
    )
    text = (
        (pytester.path / "juried.toml")
        .read_text()
        .replace("[run]", '[run]\nconcurrency_scope = "worker"')
    )
    (pytester.path / "juried.toml").write_text(text)
    scoped = pytester.runpytest("-k", "hours")
    scoped.assert_outcomes(passed=1)
    scoped.stdout.re_match_lines(
        [
            r"juried: config .*concurrency 4 target / 6 judge \(4 target / 6 judge per worker"
            r".*scope worker\)"
        ]
    )
    monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT")
    plain = pytester.runpytest("-k", "hours")
    plain.stdout.re_match_lines([r"juried: config .*concurrency 4 target / 6 judge$"])
    assert "per worker" not in plain.stdout.str()


def test_threshold_is_derived_and_deprecated(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    result = pytester.runpytest("--juried-threshold=0.1", "-v", "-k", "hours")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines(
        [
            r"juried: config .*, runs 4, misses 2, cache verdicts only.*",
            r"juried: threshold is deprecated and is removed in 0.4: threshold 0.1 with 4 runs "
            r"tolerates 2 misses, so set misses = 2 instead",
            r"juried: gate needs 2/4 passes \(2 misses tolerated\)",
        ]
    )
    text = (pytester.path / "juried.toml").read_text().replace("misses = 0", "threshold = 0.5")
    (pytester.path / "juried.toml").write_text(text)
    from_file = pytester.runpytest("-k", "hours")
    from_file.assert_outcomes(passed=1)
    from_file.stdout.re_match_lines([r"juried: threshold is deprecated.*set misses = 0 instead"])
    # The command line gate wins outright, so it cannot disagree with the file's threshold.
    overridden = pytester.runpytest("--juried-misses=1", "-k", "hours")
    overridden.assert_outcomes(passed=1)
    assert "threshold is deprecated" not in overridden.stdout.str()
    overridden.stdout.re_match_lines([r"juried: gate needs 3/4 passes \(1 miss tolerated\)"])


def test_per_scenario_gate_overrides(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    (pytester.path / "scenarios" / "c-flaky.yaml").write_text(
        """
criterion: refund-policy
scenarios:
  - name: Tolerates misses
    message: What are your opening hours?
    expected: Gives the hours including "9am".
    runs: 6
    misses: 2
  - name: Old style
    message: What are your opening hours?
    expected: Gives the hours including "9am".
    runs: 6
    threshold: 0.4
  - name: Only runs
    message: What are your opening hours?
    expected: Gives the hours including "9am".
    runs: 6
"""
    )
    result = pytester.runpytest("-v", "-k", "flaky")
    result.assert_outcomes(passed=3)
    result.stdout.fnmatch_lines(
        [
            "*tolerates-misses PASSED 6/6 (needs 4, interval *",
            "*old-style PASSED 6/6 (needs 5, interval *",
            "*only-runs PASSED 6/6 (needs 6, interval *",
        ]
    )
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    entries = {s["id"]: s for c in report["criteria"] for s in c["scenarios"]}
    assert entries["refund-policy-tolerates-misses"]["misses"] == 2
    assert entries["refund-policy-tolerates-misses"]["required_passes"] == 4
    assert entries["refund-policy-old-style"]["misses"] == 1
    assert entries["refund-policy-old-style"]["threshold"] == 0.4
    # Only runs set: the file's misses (0) applies at the scenario's own count.
    assert entries["refund-policy-only-runs"]["misses"] == 0


def test_real_judge_without_calibration_report_is_warned(
    pytester: pytest.Pytester, fake_bot_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    write_project(pytester, fake_bot_url)
    text = (
        (pytester.path / "juried.toml")
        .read_text()
        .replace('provider = "stub"', 'provider = "anthropic"')
    )
    (pytester.path / "juried.toml").write_text(text)
    result = pytester.runpytest("-k", "hours")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "juried: warning: no calibration report at *juried-calibration.json; these verdicts "
            "come from anthropic/claude-sonnet-5 and nothing has checked it against human labels.*",
            "juried: no corrected rate; run 'juried calibrate' with labelled cases to get one",
        ]
    )
    write_calibration(
        pytester,
        {
            **CALIBRATION_REPORT_STUB,
            "judge": {"provider": "anthropic", "model": "claude-haiku-4-5"},
        },
    )
    again = pytester.runpytest("-k", "hours")
    assert "no calibration report at" not in again.stdout.str()
    again.stdout.fnmatch_lines(
        [
            "*warning: *juried-calibration.json is not for this judge: it calibrated "
            "anthropic/claude-haiku-4-5, not anthropic/claude-sonnet-5"
        ]
    )


def test_stub_judge_is_not_nagged_about_calibration(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    result = pytester.runpytest("-k", "hours")
    assert "no calibration report" not in result.stdout.str()


def test_votes_switch_verdict_caching_off(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url, extra="votes = 3")
    result = pytester.runpytest("-k", "hours")
    result.assert_outcomes(passed=1)
    result.stdout.re_match_lines([r"juried: config .*\(3 votes\).*cache off \(votes > 1\).*"])
    assert not (pytester.path / ".juried" / "cache" / "verdicts").exists()
    replay = pytester.runpytest("-k", "hours", "--juried-cache-responses")
    replay.stdout.re_match_lines([r".*cache responses only, verdicts re-judged \(votes > 1\).*"])


def test_replayed_responses_are_shouted_about(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    first = pytester.runpytest("--juried-cache-responses", "-v", "-k", "hours")
    first.assert_outcomes(passed=1)
    first.stdout.re_match_lines(
        [
            r"juried: config .*, cache verdicts and responses",
            r"juried: warning: responses are replayed from the cache where present.*",
        ]
    )
    assert "replayed from" not in first.stdout.str().split("juried summary")[1]
    second = pytester.runpytest("--juried-cache-responses", "-v", "-k", "hours")
    second.assert_outcomes(passed=1)
    second.stdout.fnmatch_lines(
        [
            "*PASSED 4/4 (needs 4, interval 0.51 to 1.00) [[]4 response(s) replayed from cache[]]*",
            "*juried: warning: 4 of 4 responses were replayed from *responses and did not "
            "sample the feature; run without --cache-responses to sample again",
        ]
    )
    plain = pytester.runpytest("-v", "-k", "hours")
    plain.assert_outcomes(passed=1)
    assert "replayed from" not in plain.stdout.str()


def test_impossible_gates_are_rejected_before_any_request(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    unattainable = pytester.runpytest("--juried-threshold=0.9", "-k", "hours")
    assert unattainable.ret == pytest.ExitCode.USAGE_ERROR
    unattainable.stderr.fnmatch_lines(
        [
            "*juried: run: threshold 0.90 can never be met with 4 runs, whose best possible lower "
            "bound is 0.51; threshold is deprecated, set misses instead"
        ]
    )
    always_passes = pytester.runpytest("--juried-misses=4", "-k", "hours")
    assert always_passes.ret == pytest.ExitCode.USAGE_ERROR
    always_passes.stderr.fnmatch_lines(
        [
            "*juried: run: misses = 4 is not below runs = 4, so the gate could never fail; lower "
            "misses or raise runs"
        ]
    )
    (pytester.path / "scenarios" / "c-bad.yaml").write_text(
        "criterion: opening-hours\nscenarios:\n  - name: Lax\n    message: m\n"
        "    expected: e\n    runs: 2\n    misses: 2\n"
    )
    per_scenario = pytester.runpytest()
    per_scenario.assert_outcomes(errors=1)
    per_scenario.stdout.fnmatch_lines(
        ["*scenario 'opening-hours-lax': misses = 2 is not below runs = 2*"]
    )


def test_transport_errors_reported(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    (pytester.path / "juried.toml").write_text(
        (pytester.path / "juried.toml").read_text().replace("/chat", "/broken")
    )
    result = pytester.runpytest("-v", "-k", "hours")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "*FAILED incomplete: 0/0 judged of 4 (needs 4, interval 0.00 to 0.00) with 4 "
            "transport error(s)*",
            "juried could not complete scenario 'opening-hours-asks-hours' (Asks hours): "
            "4 of 4 attempts ended in 4 transport error(s)",
            "  these attempts are not counted in the pass rate; fix the endpoint or judge "
            "and run again",
            "*runs upheld: 0/0 judged = 0.00",
            "*gate: gate needs 4/4 passes (no misses tolerated); no attempt reached a verdict",
            "*transport errors: 4",
            "*judge errors: 0",
            "*first failing run: attempt 1 (transport error)",
            "*transport error: HTTP 500 from *",
            "1 scenarios, 0 upheld, 0 failed, 1 incomplete, 4 transport errors, 0 judge errors",
        ]
    )


def test_unset_header_variable_is_usage_error(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    (pytester.path / "juried.toml").write_text(
        (pytester.path / "juried.toml")
        .read_text()
        .replace(
            "retries = 0", 'retries = 0\nheaders = { Authorization = "Bearer ${JURIED_TEST_NOPE}" }'
        )
    )
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(
        ["*juried: [[]target[]] refers to unset environment variable JURIED_TEST_NOPE"]
    )


def test_unset_variable_in_url_or_body_is_usage_error(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    text = (pytester.path / "juried.toml").read_text()
    (pytester.path / "juried.toml").write_text(
        text.replace(
            "retries = 0",
            'retries = 0\nbody = { message = "{{message}}", tenant = "${JURIED_TEST_TENANT}" }',
        )
    )
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*unset environment variable JURIED_TEST_TENANT"])


def test_bad_response_path_is_one_clear_failure(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    (pytester.path / "juried.toml").write_text(
        (pytester.path / "juried.toml")
        .read_text()
        .replace("retries = 0", 'retries = 0\nresponse_path = "answer"')
    )
    result = pytester.runpytest("-k", "hours")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*juried: response_path 'answer': key 'answer' not found in *"])
    assert "Traceback" not in result.stdout.str()
    assert "ExceptionGroup" not in result.stdout.str()


def test_unknown_criterion_is_collection_error(
    pytester: pytest.Pytester, fake_bot_url: str
) -> None:
    write_project(pytester, fake_bot_url)
    (pytester.path / "scenarios" / "c-bad.yaml").write_text(
        "criterion: missing\nscenarios:\n  - name: x\n    message: m\n    expected: e\n"
    )
    result = pytester.runpytest()
    result.assert_outcomes(errors=1)
    result.stdout.fnmatch_lines(["*unknown criterion 'missing'; known criteria: *"])


def test_bad_config_is_usage_error(pytester: pytest.Pytester) -> None:
    pytester.makefile(".toml", juried="[run]\nruns = 1\n")
    result = pytester.runpytest()
    assert result.ret == pytest.ExitCode.USAGE_ERROR
    result.stderr.fnmatch_lines(["*juried: juried.toml is invalid*"])


def test_dormant_without_config(pytester: pytest.Pytester) -> None:
    pytester.makepyfile(test_plain="def test_ok():\n    assert True\n")
    (pytester.path / "scenarios").mkdir()
    (pytester.path / "scenarios" / "x.yaml").write_text(HOURS_SCENARIOS)
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)
    assert "juried:" not in result.stdout.str()
    assert "x.yaml" not in result.stdout.str()


def test_explicit_config_path(pytester: pytest.Pytester, fake_bot_url: str, tmp_path: Path) -> None:
    write_project(pytester, fake_bot_url)
    moved = tmp_path / "elsewhere.toml"
    moved.write_text((pytester.path / "juried.toml").read_text())
    (pytester.path / "juried.toml").unlink()
    (tmp_path / "acceptance.md").write_text(ACCEPTANCE)
    (tmp_path / "scenarios").mkdir()
    (tmp_path / "scenarios" / "s.yaml").write_text(HOURS_SCENARIOS)
    result = pytester.runpytest(f"--juried-config={moved}", str(tmp_path / "scenarios"))
    result.assert_outcomes(passed=1)
