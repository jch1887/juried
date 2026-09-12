from __future__ import annotations

import asyncio
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from juried.cache import Cache
from juried.config import Config
from juried.criteria import Criterion
from juried.judge.base import Provider, Verdict
from juried.scenarios import Scenario
from juried.stats import Interval, required_passes, wilson_interval
from juried.targets.base import Target
from juried.targets.http import HttpTarget
from juried.transport import TransportFailure

TargetFactory = Callable[[httpx.AsyncClient], Target]


@dataclass
class RunRecord:
    attempt: int
    response: str | None = None
    error: str | None = None
    verdict: Verdict | None = None
    response_cached: bool = False
    verdict_cached: bool = False
    elapsed_ms: float = 0.0
    response_ms: float | None = None

    @property
    def passed(self) -> bool:
        return self.verdict is not None and self.verdict.passed

    @property
    def outcome(self) -> str:
        if self.error is not None:
            return "transport_error"
        return "pass" if self.passed else "fail"

    @property
    def label(self) -> str:
        return self.outcome.replace("_", " ") if self.error is not None else "failed"

    @property
    def reason(self) -> str:
        if self.error is not None:
            return self.error
        return self.verdict.reason if self.verdict else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "outcome": self.outcome,
            "response": self.response,
            "error": self.error,
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "response_cached": self.response_cached,
            "verdict_cached": self.verdict_cached,
            "elapsed_ms": round(self.elapsed_ms, 1),
            "response_ms": None if self.response_ms is None else round(self.response_ms, 1),
        }


@dataclass(frozen=True)
class Latency:
    measured: int
    mean_ms: float
    max_ms: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "measured": self.measured,
            "mean_ms": round(self.mean_ms, 1),
            "max_ms": round(self.max_ms, 1),
        }


@dataclass
class ScenarioResult:
    scenario: Scenario
    criterion: Criterion
    threshold: float
    runs: list[RunRecord] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.runs)

    @property
    def passes(self) -> int:
        return sum(1 for run in self.runs if run.passed)

    @property
    def transport_errors(self) -> int:
        return sum(1 for run in self.runs if run.error is not None)

    @property
    def pass_rate(self) -> float:
        return self.passes / self.total if self.total else 0.0

    @property
    def interval(self) -> Interval:
        return wilson_interval(self.passes, self.total)

    @property
    def gate_passed(self) -> bool:
        return self.total > 0 and self.interval.lower >= self.threshold

    @property
    def required_passes(self) -> int | None:
        return required_passes(self.total, self.threshold)

    @property
    def failures(self) -> list[RunRecord]:
        return [run for run in self.runs if not run.passed]

    @property
    def latency(self) -> Latency:
        measured = [run.response_ms for run in self.runs if run.response_ms is not None]
        if not measured:
            return Latency(0, 0.0, 0.0)
        return Latency(len(measured), sum(measured) / len(measured), max(measured))

    def to_dict(self) -> dict[str, Any]:
        interval = self.interval
        return {
            "id": self.scenario.id,
            "name": self.scenario.name,
            "kind": self.scenario.kind,
            "criterion": self.criterion.id,
            "message": self.scenario.message,
            "history": [turn.model_dump() for turn in self.scenario.history],
            "expected": self.scenario.expected,
            "tags": list(self.scenario.tags),
            "source": str(self.scenario.source) if self.scenario.source else None,
            "runs": self.total,
            "passes": self.passes,
            "transport_errors": self.transport_errors,
            "pass_rate": round(self.pass_rate, 4),
            "interval": {"lower": round(interval.lower, 4), "upper": round(interval.upper, 4)},
            "threshold": self.threshold,
            "required_passes": self.required_passes,
            "gate_passed": self.gate_passed,
            "latency": self.latency.to_dict(),
            "attempts": [run.to_dict() for run in self.runs],
        }


class Runner:
    def __init__(
        self,
        config: Config,
        provider: Provider,
        cache: Cache,
        target_factory: TargetFactory | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.provider = provider
        self.cache = cache
        self.environ = environ
        self.target_factory = target_factory or self._http_target

    def _http_target(self, client: httpx.AsyncClient) -> Target:
        return HttpTarget(self.config.target, client, environ=self.environ)

    def runs_for(self, scenario: Scenario) -> int:
        return scenario.runs if scenario.runs is not None else self.config.run.runs

    def threshold_for(self, scenario: Scenario) -> float:
        return scenario.threshold if scenario.threshold is not None else self.config.run.threshold

    def run(self, scenario: Scenario, criterion: Criterion) -> ScenarioResult:
        return asyncio.run(self.run_async(scenario, criterion))

    async def run_async(self, scenario: Scenario, criterion: Criterion) -> ScenarioResult:
        runs = self.runs_for(scenario)
        semaphore = asyncio.Semaphore(self.config.run.concurrency)
        timeout = self.config.target.timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client, self.provider:
            target = self.target_factory(client)
            records = await asyncio.gather(
                *(
                    self._attempt(semaphore, target, scenario, criterion, attempt)
                    for attempt in range(1, runs + 1)
                )
            )
        return ScenarioResult(scenario, criterion, self.threshold_for(scenario), list(records))

    async def _attempt(
        self,
        semaphore: asyncio.Semaphore,
        target: Target,
        scenario: Scenario,
        criterion: Criterion,
        attempt: int,
    ) -> RunRecord:
        record = RunRecord(attempt=attempt)
        history = [turn.model_dump() for turn in scenario.history]
        started = time.perf_counter()
        async with semaphore:
            response_key = Cache.key(
                "response", target.fingerprint(), scenario.message, history, attempt
            )
            cached_response = self.cache.get("responses", response_key)
            if cached_response is not None:
                record.response = str(cached_response["text"])
                record.response_cached = True
            else:
                try:
                    response = await target.send(scenario.message, scenario.history)
                except TransportFailure as exc:
                    record.error = str(exc)
                    record.elapsed_ms = (time.perf_counter() - started) * 1000
                    return record
                record.response = response.text
                record.response_ms = response.elapsed_ms
                self.cache.put(
                    "responses",
                    response_key,
                    {"text": response.text, "status_code": response.status_code},
                )

            verdict_key = Cache.key(
                "verdict",
                self.provider.fingerprint(),
                criterion.id,
                criterion.description,
                scenario.message,
                history,
                scenario.expected,
                record.response,
            )
            cached_verdict = self.cache.get("verdicts", verdict_key)
            if cached_verdict is not None:
                record.verdict = Verdict.from_dict(cached_verdict)
                record.verdict_cached = True
            else:
                record.verdict = await self.provider.judge(criterion, scenario, record.response)
                self.cache.put("verdicts", verdict_key, record.verdict.to_dict())
        record.elapsed_ms = (time.perf_counter() - started) * 1000
        self.cache.append_log(
            "verdicts",
            {
                "scenario": scenario.id,
                "criterion": criterion.id,
                "attempt": attempt,
                "cached": record.verdict_cached,
                "temperature": self.provider.temperature,
                **record.verdict.to_dict(),
            },
        )
        return record
