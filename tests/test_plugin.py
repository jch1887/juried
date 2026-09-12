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
threshold = 0.5

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
            r"juried: config .*juried.toml, judge stub/stub, runs 4, threshold 0.5, cache on",
            r".*a-nonsense.yaml::refund-policy-asks-nonsense FAILED 0/4 \(lower 0.00 < 0.50\).*",
            r".*b-hours.yaml::opening-hours-asks-hours PASSED 4/4 \(lower 0.51 >= 0.50\).*",
            r"juried gate failed for scenario 'refund-policy-asks-nonsense' \(Asks nonsense\)",
            r"\s+criterion: refund-policy \(Refund policy\)",
            r"\s+pass rate: 0/4 = 0.00",
            r"\s+Wilson 95% interval: \[0.00, 0.49\]",
            r"\s+threshold: 0.50 on the lower bound",
            r"\s+first failing run: attempt 1 \(fail\)",
            r"\s+user: blorp",
            r"\s+response: I'm not sure about that, please contact support.",
            r"\s+judge \(stub\): response does not mention '14 days'",
            r"2 scenarios, 1 upheld, 1 failed, 0 transport errors",
            r"report: .*juried-report.html",
        ]
    )
    assert (pytester.path / "reports" / "juried-report.html").is_file()
    assert (pytester.path / "reports" / "juried-report.json").is_file()
    assert (pytester.path / ".juried" / "verdicts.jsonl").is_file()


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


def test_stops_on_first_failure(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    result = pytester.runpytest("-x")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(["*stopping after 1 failures*"])


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
    assert "pass rate: 0/4" in (failure.get("message") or "") + (failure.text or "")
    properties = {
        p.get("name"): p.get("value") for p in cases["opening-hours-asks-hours"].iter("property")
    }
    assert properties["passes"] == "4"
    assert properties["interval_lower"] == "0.5101"
    assert properties["criterion"] == "opening-hours"
    assert cases["opening-hours-asks-hours"].find("failure") is None


def test_overrides_and_cache_flag(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    result = pytester.runpytest(
        "--juried-runs=2", "--juried-threshold=0.1", "--juried-no-cache", "-v"
    )
    result.assert_outcomes(passed=1, failed=1)
    result.stdout.re_match_lines([r"juried: config .*, runs 2, threshold 0.1, cache off"])
    assert not (pytester.path / ".juried" / "cache").exists()


def test_unattainable_gate_is_flagged(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester, fake_bot_url)
    result = pytester.runpytest("--juried-threshold=0.9", "-k", "hours")
    result.assert_outcomes(failed=1)
    result.stdout.fnmatch_lines(
        [
            "juried: warning: with 4 runs the best possible lower bound is 0.51, below the "
            "threshold 0.90, so the gate can never pass.*",
            "*note: with 4 runs the best possible lower bound*",
        ]
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
            "*FAILED 0/4 (lower 0.00 < 0.50) with 4 transport error(s)*",
            "*transport errors: 4",
            "*first failing run: attempt 1 (transport_error)",
            "*transport error: HTTP 500 from *",
        ]
    )


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
