"""Pass rates corrected for the judge's own error rate, measured by `juried calibrate`."""

from __future__ import annotations

import json
import random
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from juried.config import Config
from juried.judge.prompts import PROMPT_VERSION
from juried.stats import Interval

CALIBRATION_REPORT = "juried-calibration.json"
BOOTSTRAP_SEED = 20260913
BOOTSTRAP_RESAMPLES = 2000
# Sensitivity + specificity - 1 (Youden's J) below this and the correction divides by a
# number so small that any calibration noise swamps the answer; juried refuses instead.
MIN_YOUDEN = 0.5


@dataclass(frozen=True)
class LabelledCase:
    criterion: str
    human_pass: bool
    judge_pass: bool


@dataclass(frozen=True)
class JudgeError:
    sensitivity: float
    specificity: float
    cases: int
    # "criterion" when the scenario's own criterion had enough cases, else "all".
    scope: str

    @property
    def youden(self) -> float:
        return self.sensitivity + self.specificity - 1

    @property
    def usable(self) -> bool:
        return self.youden >= MIN_YOUDEN


def judge_error(cases: Sequence[LabelledCase], scope: str) -> JudgeError | None:
    passes = [case for case in cases if case.human_pass]
    fails = [case for case in cases if not case.human_pass]
    if not passes or not fails:
        return None
    sensitivity = sum(1 for case in passes if case.judge_pass) / len(passes)
    specificity = sum(1 for case in fails if not case.judge_pass) / len(fails)
    return JudgeError(sensitivity, specificity, len(cases), scope)


@dataclass(frozen=True)
class Calibration:
    path: Path
    generated_at: str
    provider: str
    model: str
    temperature: float | None
    prompt_version: str | None
    accuracy: float
    cases: list[LabelledCase]

    def cases_for(self, criterion: str, minimum: int) -> tuple[list[LabelledCase], str]:
        own = [case for case in self.cases if case.criterion == criterion]
        if len(own) >= minimum:
            return own, "criterion"
        return list(self.cases), "all"

    def describe(self) -> str:
        return (
            f"{self.path.name} ({self.provider}/{self.model}, {len(self.cases)} cases, "
            f"{self.generated_at[:10]})"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "generated_at": self.generated_at,
            "provider": self.provider,
            "model": self.model,
            "temperature": self.temperature,
            "prompt_version": self.prompt_version,
            "cases": len(self.cases),
            "accuracy": round(self.accuracy, 4),
        }


def load_calibration(path: Path) -> Calibration | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or data.get("tool") != "juried" or "cases" not in data:
        return None
    judge = data.get("judge") or {}
    cases = [
        LabelledCase(
            str(c.get("criterion", "")), c.get("human") == "pass", c.get("judge") == "pass"
        )
        for c in data.get("cases", [])
        if isinstance(c, dict) and c.get("human") in ("pass", "fail")
    ]
    if not cases:
        return None
    temperature = judge.get("temperature")
    return Calibration(
        path,
        str(data.get("generated_at", "")),
        str(judge.get("provider", "")),
        str(judge.get("model", "")),
        float(temperature) if isinstance(temperature, int | float) else None,
        None if judge.get("prompt_version") is None else str(judge["prompt_version"]),
        float((data.get("summary") or {}).get("accuracy") or 0.0),
        cases,
    )


# A calibration report describes one judge: the same provider and model, and where the
# report recorded them, the same temperature and prompt version.
def calibration_mismatch(calibration: Calibration, config: Config) -> str | None:
    judge = config.judge
    if (calibration.provider, calibration.model) != (judge.provider, judge.model):
        return (
            f"it calibrated {calibration.provider}/{calibration.model}, not "
            f"{judge.provider}/{judge.model}"
        )
    if calibration.temperature != judge.temperature and "temperature" in _recorded(calibration):
        return f"it calibrated temperature {calibration.temperature}, not {judge.temperature}"
    if calibration.prompt_version is not None and calibration.prompt_version != PROMPT_VERSION:
        return (
            f"it calibrated judge prompt version {calibration.prompt_version}, and this is "
            f"version {PROMPT_VERSION}"
        )
    return None


def _recorded(calibration: Calibration) -> set[str]:
    # Reports from before 0.3 carry neither temperature nor prompt version; those are
    # matched on model alone rather than rejected.
    return {"temperature"} if calibration.prompt_version is not None else set()


def rogan_gladen(observed: float, sensitivity: float, specificity: float) -> float | None:
    youden = sensitivity + specificity - 1
    if youden <= 0:
        return None
    return min(1.0, max(0.0, (observed + specificity - 1) / youden))


@dataclass(frozen=True)
class Corrected:
    rate: float | None
    interval: Interval | None
    sensitivity: float
    specificity: float
    cases_used: int
    scope: str
    seed: int
    accuracy: float
    # Why no rate was produced, when it was not.
    refused: str | None = None

    @property
    def false_pass(self) -> float:
        return 1 - self.specificity

    @property
    def false_fail(self) -> float:
        return 1 - self.sensitivity

    def describe(self) -> str:
        if self.rate is None or self.interval is None:
            return f"judge too weak to correct ({self.refused})"
        return (
            f"corrected {self.rate:.0%} ({self.interval.lower:.0%} to {self.interval.upper:.0%}), "
            f"judge false pass {self.false_pass:.0%}, false fail {self.false_fail:.0%}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "corrected_rate": None if self.rate is None else round(self.rate, 4),
            "corrected_interval": (
                None
                if self.interval is None
                else {
                    "lower": round(self.interval.lower, 4),
                    "upper": round(self.interval.upper, 4),
                }
            ),
            "judge_sensitivity": round(self.sensitivity, 4),
            "judge_specificity": round(self.specificity, 4),
            "calibration_cases_used": self.cases_used,
            "calibration_scope": self.scope,
            "bootstrap_seed": self.seed,
            "corrected_refused": self.refused,
        }


def percentile(values: list[float], fraction: float) -> float:
    index = round(fraction * (len(values) - 1))
    return values[max(0, min(len(values) - 1, index))]


# Rogan-Gladen on the observed rate, with a bootstrap interval that carries both the
# sampling noise of the run and the uncertainty in how wrong the judge is. Each iteration
# resamples the calibration cases for a fresh sensitivity and specificity, and draws the
# observed rate from the Jeffreys posterior Beta(passes + 1/2, fails + 1/2) rather than
# resampling the attempts: a resample of 10/10 is always 10/10, which the clamp in
# rogan_gladen() turns into a zero width interval at 100% (and likewise at 0/10), while
# the Beta draw spreads below 1 and above 0 the way an observed 10/10 deserves. The
# percentile interval is widened to the point estimate should it ever fall outside.
def correct(
    passes: int,
    judged: int,
    calibration: Calibration,
    criterion: str,
    minimum: int,
    seed: int = BOOTSTRAP_SEED,
    resamples: int = BOOTSTRAP_RESAMPLES,
) -> Corrected | None:
    if judged == 0:
        return None
    cases, scope = calibration.cases_for(criterion, minimum)
    error = judge_error(cases, scope)
    if error is None:
        return Corrected(
            None,
            None,
            0.0,
            0.0,
            len(cases),
            scope,
            seed,
            calibration.accuracy,
            "the calibration set needs both labelled passes and labelled fails",
        )
    if not error.usable:
        return Corrected(
            None,
            None,
            error.sensitivity,
            error.specificity,
            error.cases,
            scope,
            seed,
            calibration.accuracy,
            f"sensitivity {error.sensitivity:.2f} + specificity {error.specificity:.2f} - 1 "
            f"= {error.youden:.2f}, below {MIN_YOUDEN}; calibration accuracy was "
            f"{calibration.accuracy:.2f}",
        )
    observed = passes / judged
    rate = rogan_gladen(observed, error.sensitivity, error.specificity)
    assert rate is not None
    rng = random.Random(seed)
    samples: list[float] = []
    for _ in range(resamples):
        boot_error = judge_error(rng.choices(cases, k=len(cases)), scope)
        boot_observed = rng.betavariate(passes + 0.5, judged - passes + 0.5)
        if boot_error is None:
            continue
        value = rogan_gladen(boot_observed, boot_error.sensitivity, boot_error.specificity)
        if value is not None:
            samples.append(value)
    samples.sort()
    interval = (
        Interval(min(percentile(samples, 0.025), rate), max(percentile(samples, 0.975), rate))
        if samples
        else Interval(0.0, 1.0)
    )
    return Corrected(
        rate,
        interval,
        error.sensitivity,
        error.specificity,
        error.cases,
        scope,
        seed,
        calibration.accuracy,
    )
