import json
import xml.etree.ElementTree as ET
from pathlib import Path

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
            r"interval 0.00 to 0.49\).*",
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
            r"\s+judge \(stub\): response does not mention '14 days'",
            r"2 scenarios, 1 upheld, 1 failed, 0 incomplete, 0 transport errors, 0 judge errors",
            r"judge usage: 0 input \+ 0 output tokens over 0 call\(s\), estimated \$0.0000 at "
            r"list prices of 20",
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
            "*judge (stub): response does not mention '9am'",
        ]
    )
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    scenario = report["criteria"][0]["scenarios"][0]
    assert scenario["turns"] == ["what are your hours?"]
    assert scenario["failures"][0]["transcript"][1]["content"].startswith("We are open")
    html = (pytester.path / "reports" / "juried-report.html").read_text()
    assert "user (live)</span>what are your hours?" in html
    assert "live reply, shown per run below" in html


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
            "come from anthropic/claude-sonnet-5 and nothing has checked it against human labels.*"
        ]
    )
    (pytester.path / "reports").mkdir(exist_ok=True)
    (pytester.path / "reports" / "juried-calibration.json").write_text("{}")
    again = pytester.runpytest("-k", "hours")
    assert "no calibration report" not in again.stdout.str()


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
