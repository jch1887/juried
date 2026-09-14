from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    ValidationError,
    field_validator,
    model_validator,
)

from juried.stats import Gate, GateError, build_gate

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
    # A streaming endpoint sends its reply as events; the text deltas at stream_path are
    # joined into the response and the time to the first one is recorded.
    stream: bool = False
    stream_format: Literal["sse", "ndjson"] = "sse"
    stream_path: str | None = None
    # Dotted paths to token counts in the reply, when the target reports them.
    usage_input_path: str | None = None
    usage_output_path: str | None = None
    # US dollars per million tokens, or a flat price per call for a target that reports
    # no tokens. Either prices the target side of a run; neither leaves it unknown.
    input_price: float | None = Field(default=None, ge=0.0)
    output_price: float | None = Field(default=None, ge=0.0)
    cost_per_request: float | None = Field(default=None, ge=0.0)

    @property
    def prices(self) -> tuple[float, float] | None:
        if self.input_price is None or self.output_price is None:
            return None
        return (self.input_price, self.output_price)

    @property
    def usage_paths(self) -> tuple[str, str] | None:
        if self.usage_input_path is None or self.usage_output_path is None:
            return None
        return (self.usage_input_path, self.usage_output_path)

    @property
    def priced(self) -> bool:
        return self.prices is not None or self.cost_per_request is not None

    @model_validator(mode="after")
    def streaming_is_consistent(self) -> TargetConfig:
        if self.stream and not (self.stream_path or "").strip():
            raise ValueError(
                "target.stream_path is required when stream = true: the dotted path to the "
                'text delta in each event, e.g. "choices.0.delta.content"'
            )
        if not self.stream and self.stream_path is not None:
            raise ValueError("target.stream_path needs stream = true")
        return self

    @model_validator(mode="after")
    def pricing_is_consistent(self) -> TargetConfig:
        if (self.usage_input_path is None) != (self.usage_output_path is None):
            raise ValueError(
                "target.usage_input_path and target.usage_output_path must be set together"
            )
        if (self.input_price is None) != (self.output_price is None):
            raise ValueError("target.input_price and target.output_price must be set together")
        if self.prices is not None and self.usage_paths is None:
            raise ValueError(
                "target.input_price and target.output_price need target.usage_input_path and "
                "target.usage_output_path, the paths to the token counts in the reply; for a "
                "target that reports no tokens set target.cost_per_request instead"
            )
        if self.prices is not None and self.cost_per_request is not None:
            raise ValueError(
                "set either target.cost_per_request or target.input_price and "
                "target.output_price, not both"
            )
        return self


class CriteriaConfig(StrictModel):
    file: Path = Path("acceptance.md")
    scenarios_dir: Path = Path("scenarios")
    calibration_dir: Path = Path("calibration")


class RunConfig(StrictModel):
    runs: int = Field(default=20, ge=1)
    # Failed attempts a scenario may have and still pass. Unset means DEFAULT_MISSES, or
    # whatever the deprecated threshold works out to when only that is set.
    misses: int | None = Field(default=None, ge=0)
    threshold: float | None = Field(default=None, ge=0.0, le=1.0)
    concurrency: int = Field(default=4, ge=1)
    # Under pytest-xdist each worker has its own caps. "global" divides both caps by the
    # worker count so the total in flight stays as configured; "worker" applies them as
    # written to every worker.
    concurrency_scope: Literal["global", "worker"] = "global"
    cache_dir: Path = Path(".juried")
    cache_responses: bool = False
    report_dir: Path = Path("reports")

    @model_validator(mode="after")
    def gate_is_consistent(self) -> RunConfig:
        try:
            self.gate()
        except GateError as exc:
            raise ValueError(f"run: {exc}") from None
        return self

    # A scenario may override runs, misses or threshold; the more specific setting wins,
    # and a scenario threshold is derived at the scenario's own run count.
    def gate(
        self,
        runs: int | None = None,
        misses: int | None = None,
        threshold: float | None = None,
    ) -> Gate:
        if misses is None and threshold is None:
            misses, threshold = self.misses, self.threshold
        return build_gate(runs if runs is not None else self.runs, misses, threshold)

    def with_overrides(
        self, runs: int | None = None, misses: int | None = None, threshold: float | None = None
    ) -> RunConfig:
        data = self.model_dump()
        if runs is not None:
            data["runs"] = runs
        # A command line gate replaces the file's, so the two cannot be made to disagree.
        if misses is not None:
            data["misses"], data["threshold"] = misses, None
        elif threshold is not None:
            data["misses"], data["threshold"] = None, threshold
        try:
            return RunConfig.model_validate(data)
        except ValidationError as exc:
            messages = "; ".join(str(error["msg"]) for error in exc.errors())
            raise ConfigError(messages.replace("Value error, ", "")) from None


class JudgeConfig(StrictModel):
    provider: ProviderName = "anthropic"
    model: str = "claude-sonnet-5"
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048, ge=1)
    # Any OpenAI compatible endpoint (Ollama, vLLM, LM Studio, OpenRouter, Azure OpenAI with
    # its path) works through the openai provider with its base URL here.
    base_url: str | None = None
    # The environment variable holding the key; defaults to the provider's own.
    api_key_env: str | None = None
    votes: int = Field(default=1, ge=1)
    # Quoted phrases in `expected` must match word for word, as before 0.3, rather than
    # ignoring case, spacing, hyphens and end of word punctuation.
    strict_quotes: bool = False

    @field_validator("base_url", "api_key_env")
    @classmethod
    def optional_strings_are_not_blank(cls, value: str | None, info: Any) -> str | None:
        if value is not None and not value.strip():
            raise ValueError(f"judge.{info.field_name} must not be empty; omit it for the default")
        return value.strip() if value is not None else None

    # An empty model name would otherwise reach the provider and come back as an HTTP 400
    # quoting the provider's own validator, which is far less clear than this.
    @field_validator("model")
    @classmethod
    def model_is_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("judge.model must not be empty")
        return value

    concurrency: int = Field(default=4, ge=1)
    input_price: float | None = Field(default=None, ge=0.0)
    output_price: float | None = Field(default=None, ge=0.0)

    @property
    def prices(self) -> tuple[float, float] | None:
        if self.input_price is None or self.output_price is None:
            return None
        return (self.input_price, self.output_price)

    @model_validator(mode="after")
    def prices_come_in_pairs(self) -> JudgeConfig:
        if (self.input_price is None) != (self.output_price is None):
            raise ValueError("judge.input_price and judge.output_price must be set together")
        return self

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
    temperature: float | None = Field(default=None, ge=0.0, le=2.0)
    base_url: str | None = None
    api_key_env: str | None = None
    scenarios_per_criterion: int = Field(default=4, ge=1)
    # Write juried's criterion agnostic attacks (prompt injection, system prompt
    # extraction, PII disclosure) with `generate --adversarial`, and recognise their
    # criteria in runs.
    adversarial_pack: bool = False

    @field_validator("model", "base_url", "api_key_env")
    @classmethod
    def optional_strings_are_not_blank(cls, value: str | None, info: Any) -> str | None:
        if value is not None and not value.strip():
            raise ValueError(
                f"generate.{info.field_name} must not be empty; omit it to use the judge's"
            )
        return value.strip() if value is not None else None

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

    # The judge's temperature is chosen for consistent verdicts; generation wants variety,
    # so it is not inherited. A base URL is inherited only while the provider is the same.
    @property
    def generate_temperature(self) -> float | None:
        return self.generate.temperature

    @property
    def generate_prices(self) -> tuple[float, float] | None:
        return self.judge.prices if self.generate_model == self.judge.model else None

    @property
    def generate_base_url(self) -> str | None:
        if self.generate.base_url is not None:
            return self.generate.base_url
        return self.judge.base_url if self.generate_provider == self.judge.provider else None

    @property
    def generate_api_key_env(self) -> str | None:
        if self.generate.api_key_env is not None:
            return self.generate.api_key_env
        return self.judge.api_key_env if self.generate_provider == self.judge.provider else None


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
    sections = set(Config.model_fields)
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
