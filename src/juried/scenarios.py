from __future__ import annotations

import re
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import Field, ValidationError, model_validator

from juried.config import StrictModel
from juried.criteria import slugify

ScenarioKind = Literal["happy_path", "edge_case", "custom"]
SCENARIO_SUFFIXES = (".yaml", ".yml")


class ScenarioError(Exception):
    pass


class Turn(StrictModel):
    role: Literal["user", "assistant"]
    content: str


CHECK_KINDS = ("contains", "not_contains", "regex", "json_schema", "max_latency_ms", "max_chars")


# A deterministic test of a response, run before any judge call. One key per entry.
class Check(StrictModel):
    contains: str | None = Field(default=None, min_length=1)
    not_contains: str | None = Field(default=None, min_length=1)
    regex: str | None = Field(default=None, min_length=1)
    json_schema: str | None = Field(default=None, min_length=1)
    max_latency_ms: float | None = Field(default=None, gt=0)
    max_chars: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def exactly_one_kind(self) -> Check:
        given = [kind for kind in CHECK_KINDS if getattr(self, kind) is not None]
        if len(given) != 1:
            raise ValueError(
                f"a check is one of {', '.join(CHECK_KINDS)}; got "
                f"{', '.join(given) if given else 'nothing'}"
            )
        if self.regex is not None:
            try:
                re.compile(self.regex)
            except re.error as exc:
                raise ValueError(f"regex {self.regex!r} does not compile: {exc}") from None
        return self

    @property
    def kind(self) -> str:
        return next(kind for kind in CHECK_KINDS if getattr(self, kind) is not None)

    @property
    def value(self) -> Any:
        return getattr(self, self.kind)

    def describe(self) -> str:
        return f"{self.kind} {self.value!r}"


class ScenarioDraft(StrictModel):
    name: str = Field(min_length=1)
    kind: ScenarioKind = "custom"
    message: str = Field(min_length=1)
    expected: str = Field(min_length=1)
    history: list[Turn] = Field(default_factory=list)


class Scenario(ScenarioDraft):
    id: str = Field(min_length=1)
    criterion: str = Field(min_length=1)
    # User messages sent one at a time before `message`, each answered live by the feature.
    turns: list[Annotated[str, Field(min_length=1)]] = Field(default_factory=list)
    checks: list[Check] = Field(default_factory=list)
    runs: int | None = Field(default=None, ge=1)
    misses: int | None = Field(default=None, ge=0)
    # Deprecated since 0.3: misses is derived from it. Removed in 0.4.
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    source: Path | None = Field(default=None, exclude=True)


class ScenarioFile(StrictModel):
    criterion: str | None = None
    scenarios: list[dict[str, Any]] = Field(default_factory=list)


def parse_scenario_file(text: str, source: Path | None = None) -> list[Scenario]:
    label = str(source) if source else "<string>"
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ScenarioError(f"{label} is not valid YAML: {exc}") from exc
    if raw is None:
        return []
    if not isinstance(raw, dict):
        raise ScenarioError(f"{label}: expected a mapping with a 'scenarios' list")
    try:
        parsed = ScenarioFile.model_validate(raw)
    except ValidationError as exc:
        raise ScenarioError(f"{label} is invalid:\n{exc}") from exc

    scenarios: list[Scenario] = []
    for index, entry in enumerate(parsed.scenarios):
        data = dict(entry)
        data.setdefault("criterion", parsed.criterion)
        if data.get("criterion") is None:
            raise ScenarioError(f"{label}: scenario {index + 1} has no criterion")
        if "id" not in data and isinstance(data.get("name"), str):
            data["id"] = f"{data['criterion']}-{slugify(data['name'])}"
        data["source"] = source
        try:
            scenarios.append(Scenario.model_validate(data))
        except ValidationError as exc:
            raise ScenarioError(f"{label}: scenario {index + 1} is invalid:\n{exc}") from exc
    return scenarios


def load_scenario_file(path: Path) -> list[Scenario]:
    return parse_scenario_file(path.read_text(encoding="utf-8"), path)


def scenario_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(p for p in directory.rglob("*") if p.suffix in SCENARIO_SUFFIXES and p.is_file())


def load_scenarios(directory: Path) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for path in scenario_files(directory):
        scenarios.extend(load_scenario_file(path))
    check_unique(scenarios)
    return scenarios


def check_unique(scenarios: Iterable[Scenario]) -> None:
    seen: dict[str, Path | None] = {}
    for scenario in scenarios:
        if scenario.id in seen:
            raise ScenarioError(
                f"duplicate scenario id {scenario.id!r} in {scenario.source} "
                f"and {seen[scenario.id]}"
            )
        seen[scenario.id] = scenario.source


def dump_scenario_file(criterion: str, scenarios: list[Scenario]) -> str:
    entries = []
    for scenario in scenarios:
        entry = scenario.model_dump(mode="json", exclude_none=True)
        entry.pop("criterion")
        entry = {"id": entry.pop("id"), **entry}
        if not entry.get("history"):
            entry.pop("history", None)
        if not entry.get("tags"):
            entry.pop("tags", None)
        if not entry.get("turns"):
            entry.pop("turns", None)
        if not entry.get("checks"):
            entry.pop("checks", None)
        entries.append(entry)
    document = {"criterion": criterion, "scenarios": entries}
    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True, width=88)
