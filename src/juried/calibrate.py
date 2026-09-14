from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationError

from juried import __version__, correction
from juried.config import Config, StrictModel
from juried.criteria import Criterion, slugify
from juried.judge.base import Provider, Verdict, agreement, majority_verdict
from juried.judge.prompts import PROMPT_VERSION
from juried.report.json import SCHEMA_VERSION
from juried.scenarios import SCENARIO_SUFFIXES, Scenario, Turn

# The report filename lives with the correction code, which the runner imports; this
# module imports the report package, which imports the runner, so it cannot go here.
CALIBRATION_REPORT = correction.CALIBRATION_REPORT


class CalibrationError(Exception):
    pass


class CalibrationCase(StrictModel):
    name: str = Field(min_length=1)
    criterion: str = Field(min_length=1)
    message: str = Field(min_length=1)
    history: list[Turn] = Field(default_factory=list)
    expected: str = Field(min_length=1)
    response: str
    verdict: Literal["pass", "fail"]
    note: str = ""
    source: Path | None = Field(default=None, exclude=True)

    @property
    def id(self) -> str:
        return f"{self.criterion}-{slugify(self.name)}"

    @property
    def labelled_pass(self) -> bool:
        return self.verdict == "pass"

    def as_scenario(self) -> Scenario:
        return Scenario(
            id=self.id,
            criterion=self.criterion,
            name=self.name,
            message=self.message,
            expected=self.expected,
            history=self.history,
        )


class CalibrationFile(StrictModel):
    criterion: str | None = None
    cases: list[dict[str, Any]] = Field(default_factory=list)


def parse_calibration_file(text: str, source: Path | None = None) -> list[CalibrationCase]:
    label = str(source) if source else "<string>"
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CalibrationError(f"{label} is not valid YAML: {exc}") from exc
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise CalibrationError(f"{label}: expected a mapping with a 'cases' list")
    try:
        parsed = CalibrationFile.model_validate(raw)
    except ValidationError as exc:
        raise CalibrationError(f"{label} is invalid:\n{exc}") from exc
    cases: list[CalibrationCase] = []
    for index, entry in enumerate(parsed.cases):
        data = dict(entry)
        data.setdefault("criterion", parsed.criterion)
        if data.get("criterion") is None:
            raise CalibrationError(f"{label}: case {index + 1} has no criterion")
        data["source"] = source
        try:
            cases.append(CalibrationCase.model_validate(data))
        except ValidationError as exc:
            raise CalibrationError(f"{label}: case {index + 1} is invalid:\n{exc}") from exc
    return cases


def load_calibration(directory: Path, criteria: Mapping[str, Criterion]) -> list[CalibrationCase]:
    if not directory.is_dir():
        raise CalibrationError(
            f"no calibration directory at {directory}: add YAML files of labelled responses "
            "there, each with a criterion, message, expected, response and verdict"
        )
    cases: list[CalibrationCase] = []
    for path in sorted(p for p in directory.rglob("*") if p.suffix in SCENARIO_SUFFIXES):
        cases.extend(parse_calibration_file(path.read_text(encoding="utf-8"), path))
    if not cases:
        raise CalibrationError(f"no calibration cases found under {directory}")
    for case in cases:
        if case.criterion not in criteria:
            known = ", ".join(sorted(criteria))
            raise CalibrationError(
                f"calibration case {case.id!r} refers to unknown criterion "
                f"{case.criterion!r}; known criteria: {known}"
            )
    return cases


@dataclass
class CaseOutcome:
    case: CalibrationCase
    verdict: Verdict
    votes: list[Verdict]

    @property
    def agrees(self) -> bool:
        return self.verdict.passed == self.case.labelled_pass

    @property
    def agreement(self) -> float:
        return agreement(self.votes, self.verdict)

    @property
    def kind(self) -> str:
        if self.agrees:
            return "agrees"
        return "false_pass" if self.verdict.passed else "false_fail"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case.id,
            "name": self.case.name,
            "criterion": self.case.criterion,
            "source": str(self.case.source) if self.case.source else None,
            "human": self.case.verdict,
            "judge": "pass" if self.verdict.passed else "fail",
            "outcome": self.kind,
            "reason": self.verdict.reason,
            "note": self.case.note,
            "agreement": self.agreement,
            "votes": [vote.to_dict() for vote in self.votes],
        }


@dataclass
class CalibrationResult:
    provider: str
    model: str
    votes: int
    outcomes: list[CaseOutcome] = field(default_factory=list)
    temperature: float | None = None
    prompt_version: str = PROMPT_VERSION

    @property
    def total(self) -> int:
        return len(self.outcomes)

    @property
    def agreed(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.agrees)

    @property
    def accuracy(self) -> float:
        return self.agreed / self.total if self.total else 0.0

    @property
    def false_passes(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.kind == "false_pass")

    @property
    def false_fails(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.kind == "false_fail")

    @property
    def unanimous(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.agreement == 1.0)

    @property
    def disagreements(self) -> list[CaseOutcome]:
        return [outcome for outcome in self.outcomes if not outcome.agrees]

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool": "juried",
            "schema_version": SCHEMA_VERSION,
            "version": __version__,
            "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "judge": {
                "provider": self.provider,
                "model": self.model,
                "votes": self.votes,
                "temperature": self.temperature,
                "prompt_version": self.prompt_version,
            },
            "summary": {
                "cases": self.total,
                "agreed": self.agreed,
                "accuracy": round(self.accuracy, 4),
                "false_passes": self.false_passes,
                "false_fails": self.false_fails,
                "unanimous": self.unanimous,
            },
            "cases": [outcome.to_dict() for outcome in self.outcomes],
        }


async def _judge_all(
    provider: Provider,
    criteria: Mapping[str, Criterion],
    cases: Sequence[CalibrationCase],
    votes: int,
    concurrency: int,
) -> list[CaseOutcome]:
    semaphore = asyncio.Semaphore(concurrency)

    async def vote(case: CalibrationCase) -> Verdict:
        async with semaphore:
            return await provider.judge(criteria[case.criterion], case.as_scenario(), case.response)

    async def one(case: CalibrationCase) -> CaseOutcome:
        ballots = list(await asyncio.gather(*(vote(case) for _ in range(votes))))
        return CaseOutcome(case, majority_verdict(ballots), ballots)

    async with provider:
        return list(await asyncio.gather(*(one(case) for case in cases)))


def run_calibration(
    config: Config,
    criteria: Sequence[Criterion],
    provider: Provider,
    cases: Sequence[CalibrationCase],
) -> CalibrationResult:
    # Calibration is an audit of the judge as it behaves now, so it never reads the
    # verdict cache.
    by_id = {criterion.id: criterion for criterion in criteria}
    outcomes = asyncio.run(
        _judge_all(provider, by_id, cases, config.judge.votes, config.run.concurrency)
    )
    return CalibrationResult(
        provider.name, provider.model, config.judge.votes, outcomes, provider.temperature
    )


def write_calibration_report(result: CalibrationResult, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / CALIBRATION_REPORT
    path.write_text(json.dumps(result.to_dict(), indent=2, ensure_ascii=False) + "\n", "utf-8")
    return path
