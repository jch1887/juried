import json
from pathlib import Path
from typing import Any

import pytest

from juried.cli import main
from juried.compare import CompareError, compare_reports, load_report


def entry(
    scenario_id: str, passes: int, judged: int, threshold: float = 0.7, errors: int = 0
) -> dict[str, Any]:
    from juried.stats import wilson_interval

    interval = wilson_interval(passes, judged)
    upheld = errors == 0 and interval.lower >= threshold
    status = "incomplete" if errors else ("upheld" if upheld else "failed")
    return {
        "id": scenario_id,
        "name": scenario_id.replace("-", " "),
        "criterion": "hours",
        "runs": judged + errors,
        "judged": judged,
        "passes": passes,
        "pass_rate": round(passes / judged, 4) if judged else 0.0,
        "interval": {"lower": round(interval.lower, 4), "upper": round(interval.upper, 4)},
        "threshold": threshold,
        "gate_passed": upheld,
        "status": status,
    }


def report(*entries: dict[str, Any]) -> dict[str, Any]:
    return {"tool": "juried", "criteria": [{"id": "hours", "scenarios": list(entries)}]}


def test_classifies_every_kind_of_change() -> None:
    old = report(
        entry("lost", 20, 20),
        entry("regained", 15, 20),
        entry("broken", 20, 20),
        entry("slipped", 20, 20),
        entry("better", 19, 20),
        entry("same", 19, 20),
        entry("gone", 20, 20),
    )
    new = report(
        entry("lost", 17, 20),
        entry("regained", 20, 20),
        entry("broken", 17, 17, errors=3),
        entry("slipped", 19, 20),
        entry("better", 20, 20),
        entry("same", 19, 20),
        entry("fresh-bad", 10, 20),
        entry("fresh-good", 20, 20),
    )
    changes = compare_reports(old, new)
    kinds = [(change.id, change.kind) for change in changes]
    assert kinds == [
        ("lost", "gate lost"),
        ("broken", "newly incomplete"),
        ("fresh-bad", "new failing"),
        ("slipped", "dropped"),
        ("regained", "gate regained"),
        ("better", "improved"),
        ("fresh-good", "added"),
        ("gone", "removed"),
        ("same", "unchanged"),
    ]
    assert sum(1 for change in changes if change.regression) == 4
    lost = changes[0]
    assert lost.detail == "20/20 rate 1.00 lower 0.84 upheld -> 17/20 rate 0.85 lower 0.64 failed"
    assert lost.to_dict()["old"]["gate_passed"] is True
    assert lost.to_dict()["new"]["status"] == "failed"
    assert changes[-1].to_dict()["regression"] is False


def test_tolerance_hides_small_drops() -> None:
    old = report(entry("slipped", 20, 20))
    new = report(entry("slipped", 19, 20))
    assert compare_reports(old, new)[0].kind == "dropped"
    assert compare_reports(old, new, tolerance=0.1)[0].kind == "unchanged"
    assert compare_reports(new, old, tolerance=0.1)[0].kind == "unchanged"
    assert compare_reports(new, old)[0].kind == "improved"


def test_reports_without_status_field_still_compare() -> None:
    old_entry = entry("legacy", 20, 20)
    del old_entry["status"]
    del old_entry["judged"]
    new_entry = entry("legacy", 17, 20)
    del new_entry["status"]
    change = compare_reports(report(old_entry), report(new_entry))[0]
    assert change.kind == "gate lost"
    assert change.to_dict()["old"]["judged"] == 20


def test_load_report_rejects_other_files(tmp_path: Path) -> None:
    with pytest.raises(CompareError, match="cannot read"):
        load_report(tmp_path / "missing.json")
    (tmp_path / "bad.json").write_text("{")
    with pytest.raises(CompareError, match="not valid JSON"):
        load_report(tmp_path / "bad.json")
    (tmp_path / "other.json").write_text(json.dumps({"tool": "else"}))
    with pytest.raises(CompareError, match="not a juried report"):
        load_report(tmp_path / "other.json")


def test_compare_command_prints_and_exits_nonzero_on_regression(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps(report(entry("lost", 20, 20), entry("same", 20, 20))))
    new.write_text(
        json.dumps(report(entry("lost", 17, 20), entry("same", 20, 20), entry("extra", 20, 20)))
    )
    out_json = tmp_path / "out" / "compare.json"
    assert main(["compare", str(old), str(new), "--json", str(out_json)]) == 1
    out = capsys.readouterr().out
    assert f"comparing {old} -> {new}" in out
    assert "regressions:\n  gate lost: lost: 20/20 rate 1.00 lower 0.84 upheld -> 17/20" in out
    assert "other changes:\n  added: extra: new scenario, 20/20 rate 1.00 lower 0.84 upheld" in out
    assert "1 regression(s), 0 improvement(s), 1 added or removed, 1 unchanged" in out
    data = json.loads(out_json.read_text())
    assert data["regressions"] == 1
    assert [c["kind"] for c in data["changes"]] == ["gate lost", "added"]
    assert data["unchanged"] == 1
    assert main(["compare", str(new), str(new)]) == 0
    assert "0 regression(s)" in capsys.readouterr().out
    assert main(["compare", str(old), str(tmp_path / "nope.json")]) == 2
    assert "cannot read" in capsys.readouterr().err
