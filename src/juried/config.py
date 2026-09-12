from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, ValidationError, model_validator

CONFIG_FILENAME = "juried.toml"
ENV_PREFIX = "JURIED_"

ProviderName = Literal["anthropic", "openai", "stub"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TargetConfig(StrictModel):
    url: str
    method: str = "POST"
    headers: dict[str, str] = Field(default_factory=dict)
    body: dict[str, Any] = Field(
        default_factory=lambda: {"message": "{{message}}", "history": "{{history}}"}
    )
    response_path: str = "reply"
    timeout_seconds: float = Field(default=30.0, gt=0)
    retries: int = Field(default=3, ge=0)


class CriteriaConfig(StrictModel):
    file: Path = Path("acceptance.md")
    scenarios_dir: Path = Path("scenarios")
    calibration_dir: Path = Path("calibration")


class RunConfig(StrictModel):
    runs: int = Field(default=10, ge=1)
    threshold: float = Field(default=0.7, ge=0.0, le=1.0)
    concurrency: int = Field(default=4, ge=1)
    cache_dir: Path = Path(".juried")
    cache_responses: bool = False
    report_dir: Path = Path("reports")


class JudgeConfig(StrictModel):
    provider: ProviderName = "anthropic"
    model: str = "claude-sonnet-5"
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)
    base_url: str | None = None
    votes: int = Field(default=1, ge=1)
    concurrency: int = Field(default=4, ge=1)

    @model_validator(mode="after")
    def stub_has_no_model(self) -> JudgeConfig:
        if self.provider == "stub":
            self.model = "stub"
            self.temperature = None
        return self

    @model_validator(mode="after")
    def votes_are_odd(self) -> JudgeConfig:
        if self.votes % 2 == 0:
            raise ValueError("judge.votes must be odd so that a majority always exists")
        return self


class GenerateConfig(StrictModel):
    provider: ProviderName | None = None
    model: str | None = None
    scenarios_per_criterion: int = Field(default=4, ge=1)
    max_tokens: int = Field(default=4096, ge=1)


class Config(StrictModel):
    target: TargetConfig
    criteria: CriteriaConfig = Field(default_factory=CriteriaConfig)
    run: RunConfig = Field(default_factory=RunConfig)
    judge: JudgeConfig = Field(default_factory=JudgeConfig)
    generate: GenerateConfig = Field(default_factory=GenerateConfig)

    _root: Path = PrivateAttr(default_factory=Path.cwd)

    @property
    def root(self) -> Path:
        return self._root

    def with_root(self, root: Path) -> Config:
        self._root = root.resolve()
        return self

    def resolve(self, path: Path) -> Path:
        return path if path.is_absolute() else self._root / path

    @property
    def criteria_path(self) -> Path:
        return self.resolve(self.criteria.file)

    @property
    def scenarios_path(self) -> Path:
        return self.resolve(self.criteria.scenarios_dir)

    @property
    def calibration_path(self) -> Path:
        return self.resolve(self.criteria.calibration_dir)

    @property
    def cache_path(self) -> Path:
        return self.resolve(self.run.cache_dir)

    @property
    def report_path(self) -> Path:
        return self.resolve(self.run.report_dir)

    @property
    def generate_provider(self) -> ProviderName:
        return self.generate.provider or self.judge.provider

    @property
    def generate_model(self) -> str:
        return self.generate.model or self.judge.model


class ConfigError(Exception):
    pass


def find_config(start: Path | None = None) -> Path | None:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def apply_env_overrides(data: dict[str, Any], environ: Mapping[str, str]) -> dict[str, Any]:
    sections = {name for name, field in Config.model_fields.items() if field.annotation}
    for name, value in environ.items():
        if not name.startswith(ENV_PREFIX):
            continue
        remainder = name[len(ENV_PREFIX) :].lower()
        section, _, key = remainder.partition("_")
        if section not in sections or not key:
            continue
        data.setdefault(section, {})[key] = value
    return data


def parse_config(text: str, root: Path, environ: Mapping[str, str] | None = None) -> Config:
    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{CONFIG_FILENAME} is not valid TOML: {exc}") from exc
    data = apply_env_overrides(data, os.environ if environ is None else environ)
    try:
        return Config.model_validate(data).with_root(root)
    except ValidationError as exc:
        raise ConfigError(f"{CONFIG_FILENAME} is invalid:\n{exc}") from exc


def load_config(path: Path, environ: Mapping[str, str] | None = None) -> Config:
    if not path.is_file():
        raise ConfigError(f"config file not found: {path}")
    return parse_config(path.read_text(encoding="utf-8"), path.parent, environ)
