from pathlib import Path

import pytest

from juried.calibrate import (
    CalibrationError,
    load_calibration,
    parse_calibration_file,
    run_calibration,
)
from juried.config import parse_config
from juried.criteria import Criterion
from juried.judge import StubProvider, Verdict, agreement, majority_verdict
from juried.scenarios import Scenario

HOURS = Criterion("hours", "Opening hours", "States the hours.")

CASES = """
criterion: hours
cases:
  - name: Right
    message: When are you open?
    expected: Gives the "9am" opening time.
    response: We open at 9am.
    verdict: pass
  - name: Wrong
    message: When are you open?
    history:
      - role: user
        content: hi
      - role: assistant
        content: hello
    expected: Gives the "9am" opening time.
    response: We open at nine.
    verdict: fail
    note: no digits
"""


def verdict(passed: bool, reason: str = "r") -> Verdict:
    return Verdict(passed, reason, "m", "t")


def test_majority_and_agreement() -> None:
    votes = [verdict(True, "yes"), verdict(False, "no"), verdict(True, "also yes")]
    decided = majority_verdict(votes)
    assert decided.passed and decided.reason == "yes"
    assert agreement(votes, decided) == pytest.approx(2 / 3)
    lone = majority_verdict([verdict(False, "only")])
    assert not lone.passed and agreement([lone], lone) == 1.0
    assert majority_verdict([verdict(False, "a"), verdict(False, "b"), verdict(True)]).reason == "a"


def test_parse_cases_inherit_criterion_and_become_scenarios() -> None:
    cases = parse_calibration_file(CASES, Path("c.yaml"))
    assert [case.id for case in cases] == ["hours-right", "hours-wrong"]
    assert cases[1].note == "no digits"
    assert cases[1].labelled_pass is False
    scenario = cases[1].as_scenario()
    assert isinstance(scenario, Scenario)
    assert scenario.criterion == "hours" and scenario.history[0].content == "hi"
    assert parse_calibration_file("") == []
    with pytest.raises(CalibrationError, match="case 1 has no criterion"):
        parse_calibration_file("cases:\n  - name: x\n")
    with pytest.raises(CalibrationError, match="not valid YAML"):
        parse_calibration_file("cases: [")
    with pytest.raises(CalibrationError, match="expected a mapping"):
        parse_calibration_file("- a\n")
    with pytest.raises(CalibrationError, match="verdict"):
        parse_calibration_file(CASES.replace("verdict: fail", "verdict: maybe"))


def test_load_checks_directory_and_criteria(tmp_path: Path) -> None:
    with pytest.raises(CalibrationError, match="no calibration directory"):
        load_calibration(tmp_path / "calibration", {"hours": HOURS})
    directory = tmp_path / "calibration"
    directory.mkdir()
    with pytest.raises(CalibrationError, match="no calibration cases"):
        load_calibration(directory, {"hours": HOURS})
    (directory / "hours.yml").write_text(CASES)
    assert len(load_calibration(directory, {"hours": HOURS})) == 2
    with pytest.raises(CalibrationError, match="unknown criterion 'hours'; known criteria: x"):
        load_calibration(directory, {"x": HOURS})


def test_run_calibration_with_votes(tmp_path: Path) -> None:
    config = parse_config(
        '[target]\nurl = "http://x/"\n[judge]\nprovider = "stub"\nvotes = 3\n', tmp_path
    )
    cases = parse_calibration_file(CASES)
    result = run_calibration(config, [HOURS], StubProvider(), cases)
    assert result.total == 2 and result.agreed == 2
    assert result.accuracy == 1.0
    assert result.unanimous == 2
    assert result.disagreements == []
    assert all(len(outcome.votes) == 3 for outcome in result.outcomes)
    data = result.to_dict()
    assert data["judge"]["votes"] == 3
    assert data["cases"][1]["human"] == "fail" and data["cases"][1]["judge"] == "fail"
    assert data["cases"][1]["agreement"] == 1.0
