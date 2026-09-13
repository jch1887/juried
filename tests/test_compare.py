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
    power_of,
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


def test_classifies_every_kind_of_change_in_order() -> None:
    # Gates with 8 misses hold on both sides of "slipped" and "better", so the movement is
    # judged by the test alone; "noisy" and "steady" move too little to be evidence.
    old = report(
        entry("lost", 20, 20),
        entry("regained", 15, 20),
        entry("broken", 20, 20),
        entry("slipped", 20, 20, misses=8),
        entry("noisy", 20, 20),
        entry("better", 12, 20, misses=8),
        entry("steady", 19, 20),
        entry("same", 19, 20),
        entry("gone", 20, 20),
    )
    new = report(
        entry("lost", 17, 20),
        entry("regained", 20, 20),
        entry("broken", 17, 17, errors=3),
        entry("slipped", 12, 20, misses=8),
        entry("noisy", 19, 20),
        entry("better", 20, 20, misses=8),
        entry("steady", 20, 20),
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
        ("noisy", "drop within noise"),
        ("regained", "gate regained"),
        ("better", "improved"),
        ("fresh-good", "added"),
        ("gone", "removed"),
        ("same", "unchanged"),
        ("steady", "unchanged"),
    ]
    assert sum(1 for change in changes if change.regression) == 4
    lost = changes[0]
    assert lost.detail == (
        "20/20 rate 1.00 upheld -> 17/20 rate 0.85 failed, diff -0.15 (-0.36 to +0.04), p 0.115"
    )
    assert lost.to_dict()["old"]["gate_passed"] is True
    assert lost.to_dict()["new"]["status"] == "failed"
    assert lost.to_dict()["significant"] is False
    slipped = changes[3].to_dict()
    assert slipped["p_value"] == pytest.approx(0.001638, abs=1e-6)
    assert slipped["diff"] == -0.4
    assert slipped["diff_interval"] == {"lower": -0.6134, "upper": -0.1575}
    assert slipped["significant"] is True
    noisy = changes[4]
    assert not noisy.regression
    assert noisy.difference is not None and noisy.difference.p_value == 0.5
    added = changes[7].to_dict()
    assert added["p_value"] is None and added["diff_interval"] is None
    assert changes[-1].to_dict()["regression"] is False


def test_alpha_and_min_effect_decide_significance() -> None:
    old = report(entry("slipped", 20, 20, misses=8))
    new = report(entry("slipped", 12, 20, misses=8))
    assert compare_reports(old, new)[0].kind == "dropped"
    assert compare_reports(old, new, alpha=0.001)[0].kind == "drop within noise"
    assert compare_reports(old, new, min_effect=0.5)[0].kind == "drop within noise"
    assert compare_reports(new, old)[0].kind == "improved"
    assert compare_reports(new, old, min_effect=0.5)[0].kind == "unchanged"
    small = report(entry("slipped", 19, 20, misses=8))
    assert compare_reports(old, small)[0].kind == "drop within noise"
    assert compare_reports(old, small, alpha=0.6, min_effect=0.0)[0].kind == "dropped"


def test_power_note_uses_the_smallest_scenarios() -> None:
    assert power_of([], 0.05) is None
    changes = compare_reports(
        report(entry("a", 20, 20), entry("b", 48, 50)),
        report(entry("a", 20, 20), entry("b", 47, 50), entry("c", 10, 10)),
    )
    power = power_of(changes, 0.05)
    assert power is not None
    assert (power.old_judged, power.new_judged) == (20, 20)
    assert power.drop == pytest.approx(0.34, abs=0.01)
    assert power.describe(0.05) == (
        "note: at 20 vs 20 runs this comparison can only detect drops of about 34 points or "
        "more (80% power at alpha 0.05 from a 90% pass rate); raise runs to see smaller "
        "regressions"
    )


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
    old.write_text(
        json.dumps(
            report(
                entry("lost", 20, 20),
                entry("same", 20, 20),
                entry("wobble", 20, 20, misses=8),
                entry("slid", 20, 20, misses=8),
            )
        )
    )
    new.write_text(
        json.dumps(
            report(
                entry("lost", 17, 20),
                entry("same", 20, 20),
                entry("wobble", 18, 20, misses=8),
                entry("slid", 12, 20, misses=8),
                entry("extra", 20, 20),
            )
        )
    )
    out_json = tmp_path / "out" / "compare.json"
    assert main(["compare", str(old), str(new), "--json", str(out_json)]) == 1
    out = capsys.readouterr().out
    assert f"comparing {old} -> {new} (alpha 0.05, min effect 0.1)" in out
    assert (
        "regressions:\n  gate lost: lost: 20/20 rate 1.00 upheld -> 17/20 rate 0.85 failed, "
        "diff -0.15 (-0.36 to +0.04), p 0.115\n"
        "  dropped: slid: 20/20 rate 1.00 upheld -> 12/20 rate 0.60 upheld, "
        "diff -0.40 (-0.61 to -0.16), p 0.002\n"
        "drops within noise:\n  drop within noise: wobble: 20/20 rate 1.00 upheld -> "
        "18/20 rate 0.90 upheld, diff -0.10 (-0.30 to +0.08), p 0.244\n"
    ) in out
    assert "other changes:\n  added: extra: new scenario, 20/20 rate 1.00 upheld" in out
    assert (
        "2 regression(s), 1 drop(s) within noise, 0 improvement(s), 1 added or removed, 1 unchanged"
    ) in out
    assert "note: at 20 vs 20 runs this comparison can only detect drops of about 34 points" in out
    data = json.loads(out_json.read_text())
    assert data["schema_version"] == 1
    assert (data["alpha"], data["min_effect"]) == (0.05, 0.1)
    assert data["detectable_drop"]["old_judged"] == 20
    assert data["detectable_drop"]["drop"] == pytest.approx(0.34, abs=0.01)
    assert data["regressions"] == 2
    assert data["within_noise"] == 1
    assert [c["kind"] for c in data["changes"]] == [
        "gate lost",
        "dropped",
        "drop within noise",
        "added",
    ]
    assert data["changes"][1]["significant"] is True
    assert data["changes"][2]["p_value"] == pytest.approx(0.2436, abs=1e-4)
    assert data["unchanged"] == 1
    assert main(["compare", str(new), str(new)]) == 0
    assert "0 regression(s)" in capsys.readouterr().out
    assert main(["compare", str(old), str(tmp_path / "nope.json")]) == 2
    assert "cannot read" in capsys.readouterr().err


def test_tolerance_is_a_deprecated_alias_for_min_effect(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    old, new = tmp_path / "old.json", tmp_path / "new.json"
    old.write_text(json.dumps(report(entry("slid", 20, 20, misses=8))))
    new.write_text(json.dumps(report(entry("slid", 12, 20, misses=8))))
    assert main(["compare", str(old), str(new)]) == 1
    capsys.readouterr()
    assert main(["compare", str(old), str(new), "--tolerance", "0.5"]) == 0
    captured = capsys.readouterr()
    assert "juried: --tolerance is deprecated and is removed in 0.4; use --min-effect 0.5" in (
        captured.err
    )
    assert "(alpha 0.05, min effect 0.5)" in captured.out
    assert "drop within noise: slid" in captured.out
    assert main(["compare", str(old), str(new), "--min-effect", "0.5"]) == 0
    assert "deprecated" not in capsys.readouterr().err
    assert main(["compare", str(old), str(new), "--tolerance", "0.5", "--min-effect", "0.5"]) == 0
    assert main(["compare", str(old), str(new), "--tolerance", "0.5", "--min-effect", "0.1"]) == 2
    assert "disagree" in capsys.readouterr().err
    assert main(["compare", str(old), str(new), "--alpha", "1"]) == 2
    assert "--alpha must be between 0 and 1" in capsys.readouterr().err
    assert main(["compare", str(old), str(new), "--alpha", "0.001"]) == 0
