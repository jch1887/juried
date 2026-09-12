import json
import sys
from pathlib import Path
from typing import Any

import pytest

from juried.cli import main

ACCEPTANCE = "# Criteria\n\n## Opening hours\nStates the hours, 9am to 5pm.\n"


def test_init_writes_files_and_keeps_existing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / ".gitignore").write_text("*.pyc")
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "juried.toml").is_file()
    assert (tmp_path / "acceptance.md").is_file()
    assert (tmp_path / "scenarios").is_dir()
    assert (tmp_path / ".gitignore").read_text() == "*.pyc\n.juried/\nreports/\n"
    (tmp_path / "acceptance.md").write_text("custom")
    assert main(["init", "--dir", str(tmp_path)]) == 0
    assert (tmp_path / "acceptance.md").read_text() == "custom"
    assert "kept existing" in capsys.readouterr().out
    assert main(["init", "--dir", str(tmp_path), "--force"]) == 0
    assert (tmp_path / "acceptance.md").read_text().startswith("# Acceptance criteria")
    assert (tmp_path / ".gitignore").read_text() == "*.pyc\n.juried/\nreports/\n"


def test_init_config_is_valid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    main(["init", "--dir", str(tmp_path)])
    from juried.config import load_config
    from juried.criteria import load_criteria

    config = load_config(tmp_path / "juried.toml", environ={})
    assert config.run.threshold == 0.7
    assert [c.id for c in load_criteria(config.criteria_path)] == [
        "opening-hours",
        "unknown-questions",
    ]


def write_project(directory: Path, url: str) -> None:
    (directory / "juried.toml").write_text(
        f'[target]\nurl = "{url}/chat"\n[run]\nruns = 3\nthreshold = 0.2\n'
        '[judge]\nprovider = "stub"\n'
    )
    (directory / "acceptance.md").write_text(ACCEPTANCE)


def test_generate_then_run(
    tmp_path: Path, fake_bot_url: str, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    write_project(tmp_path, fake_bot_url)
    monkeypatch.chdir(tmp_path)
    assert main(["generate"]) == 0
    out = capsys.readouterr().out
    assert "generating scenarios with stub/stub" in out
    assert "usage: 0 input + 0 output tokens over 0 call(s), estimated $0.0000" in out
    assert "opening-hours.yaml (2 scenarios)" in out
    assert main(["generate"]) == 0
    assert "kept existing" in capsys.readouterr().out
    assert main(["generate", "--criterion", "nope"]) == 2
    assert "unknown criteria: nope" in capsys.readouterr().err

    captured: dict[str, list[str]] = {}

    def fake_pytest_main(args: list[str]) -> int:
        captured["args"] = args
        return 0

    monkeypatch.setattr("juried.cli.pytest.main", fake_pytest_main)
    assert main(["run", "--runs", "5", "-k", "x", "--threshold", "0.3", "--no-cache", "-x"]) == 0
    args = captured["args"]
    assert args[0] == f"--juried-config={tmp_path / 'juried.toml'}"
    assert args[1] == f"--rootdir={tmp_path}"
    assert args[2:6] == ["-v", "--juried-runs=5", "--juried-threshold=0.3", "--juried-no-cache"]
    assert args[6] == str(tmp_path / "scenarios")
    assert args[7:] == ["-k", "x", "-x"]
    assert main(["run", "--cache-responses"]) == 0
    assert "--juried-cache-responses" in captured["args"]
    assert main(["run"]) == 0
    assert "--juried-cache-responses" not in captured["args"]
    assert main(["run", "--", "scenarios"]) == 0
    assert captured["args"][-1] == "scenarios"
    assert str(tmp_path / "scenarios") not in captured["args"]


CALIBRATION = """
criterion: opening-hours
cases:
  - name: Right
    message: When are you open?
    expected: Gives the "9am" opening time.
    response: We open at 9am.
    verdict: pass
  - name: Wrong words
    message: When are you open?
    expected: Gives the "9am" opening time.
    response: We open at nine.
    verdict: fail
  - name: Right words wrong answer
    message: When are you open?
    expected: Gives the "9am" opening time.
    response: We never open at 9am, only at noon.
    verdict: fail
    note: stub cannot see this
"""


def test_calibrate_reports_disagreements(
    tmp_path: Path, fake_bot_url: str, monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    write_project(tmp_path, fake_bot_url)
    monkeypatch.chdir(tmp_path)
    assert main(["calibrate"]) == 2
    assert "no calibration directory" in capsys.readouterr().err
    (tmp_path / "calibration").mkdir()
    (tmp_path / "calibration" / "hours.yaml").write_text(CALIBRATION)
    assert main(["calibrate"]) == 0
    out = capsys.readouterr().out
    assert "calibrating stub/stub against 3 labelled response(s)" in out
    assert (
        "  false pass: opening-hours-right-words-wrong-answer: human says fail, judge says "
        "pass: response mentions every expected phrase [stub cannot see this]"
    ) in out
    assert (
        "judge agreed with the human label on 2/3 (0.67): 1 false pass(es), 0 false fail(s)"
    ) in out
    assert "unanimous" not in out
    report = json.loads((tmp_path / "reports" / "juried-calibration.json").read_text())
    assert report["summary"] == {
        "cases": 3,
        "agreed": 2,
        "accuracy": 0.6667,
        "false_passes": 1,
        "false_fails": 0,
        "unanimous": 3,
    }
    assert report["judge"] == {"provider": "stub", "model": "stub", "votes": 1}
    assert [case["outcome"] for case in report["cases"]] == ["agrees", "agrees", "false_pass"]
    assert main(["calibrate", "--min-accuracy", "0.9"]) == 1
    assert "judge accuracy 0.67 is below 0.90" in capsys.readouterr().out
    (tmp_path / "calibration" / "bad.yaml").write_text("criterion: nope\ncases:\n  - name: x\n")
    assert main(["calibrate"]) == 2
    assert "case 1 is invalid" in capsys.readouterr().err


def test_run_end_to_end_in_subprocess(pytester: pytest.Pytester, fake_bot_url: str) -> None:
    write_project(pytester.path, fake_bot_url)
    scenarios = pytester.mkdir("scenarios")
    (scenarios / "hours.yaml").write_text(
        "criterion: opening-hours\nscenarios:\n  - name: Asks hours\n"
        '    message: opening hours?\n    expected: Mentions "9am".\n'
        '  - name: Nonsense\n    message: blorp\n    expected: Mentions "9am".\n'
    )
    result = pytester.run(sys.executable, "-m", "juried.cli", "run", "--junitxml=out.xml")
    assert result.ret == 1
    result.stdout.fnmatch_lines(
        [
            "*opening-hours-asks-hours PASSED 3/3 (lower 0.44 >= 0.20)*",
            "*opening-hours-nonsense FAILED 0/3 (lower 0.00 < 0.20)*",
            "*2 scenarios, 1 upheld, 1 failed, 0 incomplete, 0 transport errors, 0 judge errors",
        ]
    )
    report = json.loads((pytester.path / "reports" / "juried-report.json").read_text())
    assert report["summary"]["gates_failed"] == 1
    assert report["summary"]["incomplete"] == 0
    assert (pytester.path / "out.xml").is_file()


def test_missing_config_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    assert main(["generate"]) == 2
    assert "no juried.toml found" in capsys.readouterr().err
    assert main(["run", "--config", str(tmp_path / "nope.toml")]) == 2
    assert "not found" in capsys.readouterr().err


def test_target_config_errors_are_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from juried.targets.http import TargetConfigError

    def explode(args: list[str]) -> int:
        raise TargetConfigError("header refers to unset environment variable TOKEN")

    (tmp_path / "juried.toml").write_text('[target]\nurl = "http://x/"\n')
    (tmp_path / "acceptance.md").write_text(ACCEPTANCE)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("juried.cli.pytest.main", explode)
    assert main(["run"]) == 2
    assert "juried: header refers to unset environment variable TOKEN" in capsys.readouterr().err
    with pytest.raises(SystemExit):
        main(["init", "--bogus"])
