from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import ValidationError

from juried.criteria import Criterion
from juried.judge import prompts
from juried.scenarios import Scenario, ScenarioDraft
from juried.transport import TransportFailure, request_json

JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


class ProviderError(Exception):
    pass


@dataclass(frozen=True)
class Verdict:
    passed: bool
    reason: str
    model: str
    judged_at: str

    @staticmethod
    def now() -> str:
        return datetime.now(UTC).isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "reason": self.reason,
            "model": self.model,
            "judged_at": self.judged_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Verdict:
        return cls(
            bool(data["passed"]), str(data["reason"]), str(data["model"]), str(data["judged_at"])
        )


class Provider(ABC):
    name: str
    model: str
    temperature: float | None = None

    @abstractmethod
    def fingerprint(self) -> str: ...

    @abstractmethod
    async def judge(
        self, criterion: Criterion, scenario: Scenario, response_text: str
    ) -> Verdict: ...

    @abstractmethod
    async def generate(self, criterion: Criterion, count: int) -> list[ScenarioDraft]: ...

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


class LLMProvider(Provider):
    def __init__(
        self,
        model: str,
        temperature: float | None,
        max_tokens: int,
        base_url: str | None = None,
        timeout_seconds: float = 120.0,
    ) -> None:
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.base_url = base_url
        self.timeout_seconds = timeout_seconds
        self._client: httpx.AsyncClient | None = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._client

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def fingerprint(self) -> str:
        return json.dumps(
            {
                "provider": self.name,
                "model": self.model,
                "temperature": self.temperature,
                "prompt_version": prompts.PROMPT_VERSION,
            },
            sort_keys=True,
        )

    async def post_json(self, url: str, headers: dict[str, str], body: Any) -> httpx.Response:
        try:
            return await request_json(
                self.client, "POST", url, headers=headers, body=body, retries=3
            )
        except TransportFailure as exc:
            if (
                self.temperature is not None
                and exc.status_code == 400
                and "temperature" in str(exc).lower()
            ):
                raise ProviderError(
                    f"{self.model} does not accept temperature; remove temperature from "
                    f"[judge] in juried.toml to use this model"
                ) from exc
            raise

    @abstractmethod
    async def complete_json(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]: ...

    async def judge(self, criterion: Criterion, scenario: Scenario, response_text: str) -> Verdict:
        data = await self.complete_json(
            prompts.JUDGE_SYSTEM,
            prompts.judge_user_prompt(criterion, scenario, response_text),
            prompts.JUDGE_SCHEMA,
            self.max_tokens,
        )
        if not isinstance(data.get("pass"), bool):
            raise ProviderError(f"judge returned no boolean 'pass' field: {data!r}")
        reason = str(data.get("reason", "")).strip() or "no reason given"
        return Verdict(data["pass"], reason, self.model, Verdict.now())

    async def generate(self, criterion: Criterion, count: int) -> list[ScenarioDraft]:
        data = await self.complete_json(
            prompts.GENERATE_SYSTEM,
            prompts.generate_user_prompt(criterion, count),
            prompts.GENERATE_SCHEMA,
            max(self.max_tokens, 4096),
        )
        entries = data.get("scenarios")
        if not isinstance(entries, list):
            raise ProviderError(f"generator returned no 'scenarios' list: {data!r}")
        try:
            return [ScenarioDraft.model_validate(entry) for entry in entries]
        except ValidationError as exc:
            raise ProviderError(f"generator returned an invalid scenario:\n{exc}") from exc


def parse_json_object(text: str) -> dict[str, Any]:
    candidates = [text.strip()]
    match = JSON_OBJECT.search(text)
    if match:
        candidates.append(match.group(0))
    for candidate in candidates:
        try:
            loaded = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            return loaded
    raise ProviderError(f"model did not return a JSON object: {text[:300]!r}")


def require_env(environ: dict[str, str], name: str, provider: str) -> str:
    value = environ.get(name, "").strip()
    if not value:
        raise ProviderError(f"{provider} provider needs the {name} environment variable")
    return value
