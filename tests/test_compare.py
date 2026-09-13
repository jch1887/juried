import json
import re
from pathlib import Path
from typing import Any

import pytest

from juried.cli import main
from juried.compare import (
    CompareError,
    check_same_schema,
    compare_reports,
    load_report,
    summary,
)


def entry(
    scenario_id: str, passes: int, judged: int, misses: int = 1, errors: int = 0
) -> dict[str, Any]:
    from juried.stats import wilson_interval

    interval = wilson_interval(passes, judged)
    upheld = errors == 0 and judged - passes <= misses
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
        "misses": misses,
        "required_passes": judged + errors - misses,
        "gate_passed": upheld,
        "status": status,
    }


def report(*entries: dict[str, Any], schema: int | None = 1) -> dict[str, Any]:
    data: dict[str, Any] = {
        "tool": "juried",
        "criteria": [{"id": "hours", "scenarios": list(entries)}],
    }
    if schema is not None:
        data["schema_version"] = schema
    return data


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
    assert change.to_dict()["old"]["passes_needed"] == 19


def test_passes_needed_is_read_from_misses_threshold_or_required_passes() -> None:
    from_misses = summary(entry("x", 20, 20, misses=3))
    assert from_misses is not None
    assert from_misses["passes_needed"] == 17
    from_required = entry("x", 20, 20)
    del from_required["misses"]
    from_required["required_passes"] = 18
    assert summary(from_required)["passes_needed"] == 18  # type: ignore[index]
    from_threshold = entry("x", 20, 20)
    del from_threshold["misses"]
    del from_threshold["required_passes"]
    from_threshold["threshold"] = 0.7
    assert summary(from_threshold)["passes_needed"] == 19  # type: ignore[index]
    del from_threshold["threshold"]
    assert summary(from_threshold)["passes_needed"] is None  # type: ignore[index]


def test_reports_with_different_schema_versions_are_refused(tmp_path: Path) -> None:
    old, new = Path("old.json"), Path("new.json")
    check_same_schema(old, report(schema=1), new, report(schema=1))
    check_same_schema(old, report(schema=None), new, report(schema=None))
    with pytest.raises(
        CompareError,
        match=re.escape("old.json has schema_version 1, new.json has schema_version 2"),
    ):
        check_same_schema(old, report(schema=1), new, report(schema=2))
    with pytest.raises(
        CompareError,
        match=re.escape("old.json has no schema_version (written before juried 0.2.0)"),
    ):
        check_same_schema(old, report(schema=None), new, report(schema=1))
    legacy = tmp_path / "legacy.json"
    current = tmp_path / "current.json"
    legacy.write_text(json.dumps(report(entry("same", 20, 20), schema=None)))
    current.write_text(json.dumps(report(entry("same", 20, 20))))
    assert main(["compare", str(legacy), str(current)]) == 2
    assert main(["compare", str(current), str(current)]) == 0


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
