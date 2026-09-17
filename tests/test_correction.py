import json
from pathlib import Path

import pytest

from juried.config import parse_config
from juried.correction import (
    BOOTSTRAP_SEED,
    Calibration,
    LabelledCase,
    calibration_mismatch,
    correct,
    judge_error,
    load_calibration,
    rogan_gladen,
)
from juried.judge.prompts import PROMPT_VERSION
from juried.stats import wilson_interval


def cases(criterion: str, passes: tuple[int, int], fails: tuple[int, int]) -> list[LabelledCase]:
    # passes = (judged pass, judged fail) among human passes; fails likewise among human fails.
    return (
        [LabelledCase(criterion, True, True)] * passes[0]
        + [LabelledCase(criterion, True, False)] * passes[1]
        + [LabelledCase(criterion, False, False)] * fails[0]
        + [LabelledCase(criterion, False, True)] * fails[1]
    )


def calibration(labelled: list[LabelledCase], accuracy: float = 0.9) -> Calibration:
    return Calibration(
        Path("reports/juried-calibration.json"),
        "2026-09-13T05:55:53+00:00",
        "anthropic",
        "claude-haiku-4-5",
        None,
        PROMPT_VERSION,
        accuracy,
        labelled,
    )


def test_rogan_gladen_hand_checked() -> None:
    # 90% observed, judge passes 97% of true passes and fails 94% of true fails:
    # (0.9 + 0.94 - 1) / (0.97 + 0.94 - 1) = 0.84 / 0.91.
    assert rogan_gladen(0.9, 0.97, 0.94) == pytest.approx(0.84 / 0.91)
    # A perfect judge changes nothing.
    assert rogan_gladen(0.75, 1.0, 1.0) == 0.75
    # Observed below the false pass rate clamps to 0; above the sensitivity clamps to 1.
    assert rogan_gladen(0.05, 0.9, 0.9) == 0.0
    assert rogan_gladen(0.95, 0.9, 0.9) == 1.0
    # A judge no better than chance has no correction.
    assert rogan_gladen(0.5, 0.5, 0.5) is None
    assert rogan_gladen(0.5, 0.3, 0.6) is None


def test_judge_error_needs_both_labels() -> None:
    error = judge_error(cases("c", (9, 1), (8, 2)), "criterion")
    assert error is not None
    assert (error.sensitivity, error.specificity, error.cases) == (0.9, 0.8, 20)
    assert error.youden == pytest.approx(0.7) and error.usable
    assert judge_error(cases("c", (5, 0), (0, 0)), "all") is None
    weak = judge_error(cases("c", (7, 3), (7, 3)), "all")
    assert weak is not None and not weak.usable


def test_correct_falls_back_from_criterion_to_whole_set() -> None:
    own = cases("refunds", (4, 0), (4, 0))  # 8 cases, under the minimum of 10
    rest = cases("hours", (18, 2), (14, 6))
    cal = calibration(own + rest)
    borrowed = correct(18, 20, cal, "refunds", minimum=10)
    assert borrowed is not None and borrowed.scope == "all" and borrowed.cases_used == 48
    assert borrowed.sensitivity == pytest.approx(22 / 24)
    assert borrowed.specificity == pytest.approx(18 / 24)
    lowered = correct(18, 20, cal, "refunds", minimum=8)
    assert lowered is not None and lowered.scope == "criterion" and lowered.cases_used == 8
    assert (lowered.sensitivity, lowered.specificity) == (1.0, 1.0)
    assert lowered.rate == pytest.approx(0.9)
    assert correct(0, 0, cal, "refunds", 10) is None


def test_correct_refuses_a_weak_judge_and_says_why() -> None:
    weak = correct(18, 20, calibration(cases("c", (7, 3), (7, 3)), accuracy=0.7), "c", 10)
    assert weak is not None
    assert weak.rate is None and weak.interval is None
    assert weak.refused == (
        "sensitivity 0.70 + specificity 0.70 - 1 = 0.40, below 0.5; calibration accuracy was 0.70"
    )
    assert weak.describe().startswith("judge too weak to correct (sensitivity 0.70")
    assert weak.to_dict()["corrected_rate"] is None
    one_sided = correct(18, 20, calibration(cases("c", (10, 0), (0, 0))), "c", 10)
    assert one_sided is not None and "both labelled passes" in (one_sided.refused or "")


def test_bootstrap_is_deterministic_under_a_fixed_seed() -> None:
    cal = calibration(cases("c", (30, 1), (28, 3)))
    first = correct(18, 20, cal, "c", 10)
    second = correct(18, 20, cal, "c", 10)
    assert first is not None and second is not None
    assert first == second
    assert first.seed == BOOTSTRAP_SEED
    assert first.interval is not None
    assert first.interval.lower < first.rate < first.interval.upper  # type: ignore[operator]
    assert first.interval.lower >= 0.0 and first.interval.upper <= 1.0
    other = correct(18, 20, cal, "c", 10, seed=7)
    assert other is not None and other.interval != first.interval
    assert other.rate == first.rate
    text = first.describe()
    assert text.startswith("corrected 9") and "judge false pass 10%, false fail 3%" in text
    data = first.to_dict()
    assert set(data) == {
        "corrected_rate",
        "corrected_interval",
        "judge_sensitivity",
        "judge_specificity",
        "calibration_cases_used",
        "calibration_scope",
        "bootstrap_seed",
        "corrected_refused",
    }


def test_interval_keeps_its_width_at_all_passes_and_at_none() -> None:
    # A judge that passes 2 of 7 bad responses (false pass 29%) and every good one. Ten
    # of ten observed is not certainty, and a judge that false passes pulls the rate down,
    # so the corrected lower bound sits below the observed Wilson lower bound.
    false_pass = correct(10, 10, calibration(cases("c", (3, 0), (5, 2))), "c", 10)
    assert false_pass is not None and false_pass.rate == 1.0
    assert false_pass.interval is not None
    assert false_pass.interval.lower < wilson_interval(10, 10).lower
    assert false_pass.interval.lower == pytest.approx(0.6677, abs=1e-4)
    # No bootstrap sample reaches the clamped point estimate, so the upper bound is
    # widened to it rather than reporting 100% outside its own interval.
    assert false_pass.interval.upper == 1.0
    assert false_pass.describe().startswith("corrected 100% (67% to 100%)")
    # The mirror: a judge that fails 2 of 7 good responses pushes 0 of 10 up.
    false_fail = correct(0, 10, calibration(cases("c", (5, 2), (3, 0))), "c", 10)
    assert false_fail is not None and false_fail.rate == 0.0
    assert false_fail.interval is not None
    assert false_fail.interval.lower == 0.0
    assert false_fail.interval.upper > wilson_interval(0, 10).upper
    assert false_fail.interval.upper == pytest.approx(0.3414, abs=1e-4)


def test_interval_contains_the_point_estimate() -> None:
    mid = correct(7, 10, calibration(cases("c", (30, 1), (28, 3))), "c", 10)
    assert mid is not None and mid.rate is not None and mid.interval is not None
    assert mid.rate == pytest.approx(0.6926, abs=1e-4)
    assert mid.interval.lower < mid.rate < mid.interval.upper
    assert mid.interval.lower == pytest.approx(0.3426, abs=1e-4)
    assert mid.interval.upper == pytest.approx(0.9435, abs=1e-4)


def test_attempts_are_drawn_not_resampled() -> None:
    # A judge the calibration set always agrees with: every resample of the cases gives
    # sensitivity 1 and specificity 1, so the only randomness left is the draw of the
    # observed rate. Same seed, same interval; another seed, another interval.
    perfect = calibration(cases("c", (10, 0), (10, 0)))
    first = correct(10, 10, perfect, "c", 10)
    again = correct(10, 10, perfect, "c", 10)
    other = correct(10, 10, perfect, "c", 10, seed=7)
    assert first is not None and again is not None and other is not None
    assert first.interval == again.interval
    assert first.interval is not None and other.interval is not None
    assert first.interval != other.interval
    assert first.interval.lower == pytest.approx(0.7777, abs=1e-4)
    assert other.interval.lower == pytest.approx(0.7900, abs=1e-4)
    assert first.interval.upper == other.interval.upper == 1.0


def test_calibration_report_round_trip_and_matching(tmp_path: Path) -> None:
    report = tmp_path / "juried-calibration.json"
    report.write_text(
        json.dumps(
            {
                "tool": "juried",
                "generated_at": "2026-09-13T05:55:53+00:00",
                "judge": {"provider": "anthropic", "model": "claude-haiku-4-5", "votes": 1},
                "summary": {"accuracy": 0.9},
                "cases": [
                    {"criterion": "c", "human": "pass", "judge": "pass"},
                    {"criterion": "c", "human": "fail", "judge": "pass"},
                    {"criterion": "d", "human": "fail", "judge": "fail"},
                ],
            }
        )
    )
    loaded = load_calibration(report)
    assert loaded is not None
    assert [(c.criterion, c.human_pass, c.judge_pass) for c in loaded.cases] == [
        ("c", True, True),
        ("c", False, True),
        ("d", False, False),
    ]
    assert (
        loaded.describe()
        == "juried-calibration.json (anthropic/claude-haiku-4-5, 3 cases, 2026-09-13)"
    )
    assert loaded.to_dict()["accuracy"] == 0.9
    base = '[target]\nurl = "http://x/"\n[judge]\nprovider = "anthropic"\n'
    haiku = parse_config(base + 'model = "claude-haiku-4-5"\n', tmp_path, environ={})
    assert calibration_mismatch(loaded, haiku) is None
    sonnet = parse_config(base + 'model = "claude-sonnet-5"\n', tmp_path, environ={})
    assert calibration_mismatch(loaded, sonnet) == (
        "it calibrated anthropic/claude-haiku-4-5, not anthropic/claude-sonnet-5"
    )
    # A 0.2 report records neither temperature nor prompt version and matches on model.
    warm = parse_config(base + 'model = "claude-haiku-4-5"\ntemperature = 0.5\n', tmp_path)
    assert calibration_mismatch(loaded, warm) is None
    recorded = Calibration(
        report, "", "anthropic", "claude-haiku-4-5", 0.0, PROMPT_VERSION, 0.9, loaded.cases
    )
    assert calibration_mismatch(recorded, warm) == "it calibrated temperature 0.0, not 0.5"
    stale = Calibration(report, "", "anthropic", "claude-haiku-4-5", None, "1", 0.9, loaded.cases)
    assert calibration_mismatch(stale, haiku) is not None
    assert "prompt version 1" in (calibration_mismatch(stale, haiku) or "")
    assert load_calibration(tmp_path / "missing.json") is None
    (tmp_path / "other.json").write_text(json.dumps({"tool": "else"}))
    assert load_calibration(tmp_path / "other.json") is None
