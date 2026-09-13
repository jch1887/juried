from __future__ import annotations

import asyncio
import concurrent.futures
import threading
import time
from collections.abc import AsyncIterator, Callable, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any

import httpx

from juried.cache import Cache
from juried.config import Config
from juried.criteria import Criterion
from juried.judge.base import Provider, ProviderError, Verdict, agreement, majority_verdict
from juried.pricing import Usage
from juried.scenarios import Scenario, Turn
from juried.stats import Gate, Interval, wilson_interval
from juried.targets.base import Target
from juried.targets.http import HttpTarget, TargetConfigError
from juried.transport import TransportFailure

TargetFactory = Callable[[httpx.AsyncClient], Target]


@dataclass
class RunRecord:
    attempt: int
    response: str | None = None
    error: str | None = None
    judge_error: str | None = None
    verdict: Verdict | None = None
    votes: list[Verdict] = field(default_factory=list)
    fresh_votes: list[Usage] = field(default_factory=list)
    # Live turns before the final message: the user messages from `turns` and the feature's
    # replies to them, in order.
    transcript: list[Turn] = field(default_factory=list)
    response_cached: bool = False
    verdict_cached: bool = False
    elapsed_ms: float = 0.0
    response_ms: float | None = None

    @property
    def passed(self) -> bool:
        return self.verdict is not None and self.verdict.passed

    @property
    def agreement(self) -> float | None:
        if self.verdict is None:
            return None
        return agreement(self.votes, self.verdict) if self.votes else 1.0

    @property
    def errored(self) -> bool:
        return self.error is not None or self.judge_error is not None

    @property
    def usage(self) -> Usage:
        # Cached votes cost nothing this run, so only fresh votes count.
        return sum(self.fresh_votes, Usage())

    @property
    def outcome(self) -> str:
        if self.error is not None:
            return "transport_error"
        if self.judge_error is not None:
            return "judge_error"
        return "pass" if self.passed else "fail"

    @property
    def label(self) -> str:
        return self.outcome.replace("_", " ") if self.errored else "failed"

    @property
    def reason(self) -> str:
        if self.error is not None:
            return self.error
        if self.judge_error is not None:
            return self.judge_error
        return self.verdict.reason if self.verdict else ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempt": self.attempt,
            "outcome": self.outcome,
            "response": self.response,
            "transcript": [turn.model_dump() for turn in self.transcript],
            "error": self.error,
            "judge_error": self.judge_error,
            "verdict": self.verdict.to_dict() if self.verdict else None,
            "votes": [vote.to_dict() for vote in self.votes],
            "agreement": self.agreement,
            "usage": self.usage.to_dict(),
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
    gate: Gate
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
    def responses_from_cache(self) -> int:
        return sum(1 for run in self.runs if run.response_cached)

    @property
    def judge_errors(self) -> int:
        return sum(1 for run in self.runs if run.judge_error is not None)

    @property
    def errors(self) -> int:
        return self.transport_errors + self.judge_errors

    @property
    def judged(self) -> int:
        return self.total - self.errors

    @property
    def fails(self) -> int:
        return self.judged - self.passes

    # Pass rate, interval and the gate describe the feature's quality, so they are computed
    # over the attempts that reached a verdict. Attempts lost to transport or judge errors
    # are counted separately and make the result incomplete rather than dragging the rate
    # down. The interval is descriptive; the gate is the count of misses.
    @property
    def pass_rate(self) -> float:
        return self.passes / self.judged if self.judged else 0.0

    @property
    def interval(self) -> Interval:
        return wilson_interval(self.passes, self.judged)

    @property
    def quality_met(self) -> bool:
        return self.gate.met(self.passes, self.judged)

    @property
    def complete(self) -> bool:
        return self.errors == 0

    @property
    def gate_passed(self) -> bool:
        return self.complete and self.quality_met

    @property
    def status(self) -> str:
        if not self.complete:
            return "incomplete"
        return "upheld" if self.quality_met else "failed"

    @property
    def misses(self) -> int:
        return self.gate.misses

    @property
    def required_passes(self) -> int:
        return self.gate.passes_needed

    @property
    def threshold(self) -> float:
        return self.gate.equivalent_threshold

    @property
    def failures(self) -> list[RunRecord]:
        return [run for run in self.runs if not run.passed]

    @property
    def usage(self) -> Usage:
        return sum((run.usage for run in self.runs), Usage())

    @property
    def judge_agreement(self) -> float | None:
        judged = [run.agreement for run in self.runs if run.agreement is not None]
        return sum(judged) / len(judged) if judged else None

    @property
    def split_verdicts(self) -> int:
        return sum(1 for run in self.runs if run.agreement is not None and run.agreement < 1.0)

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
            "turns": list(self.scenario.turns),
            "expected": self.scenario.expected,
            "tags": list(self.scenario.tags),
            "source": str(self.scenario.source) if self.scenario.source else None,
            "runs": self.total,
            "judged": self.judged,
            "passes": self.passes,
            "transport_errors": self.transport_errors,
            "responses_from_cache": self.responses_from_cache,
            "judge_errors": self.judge_errors,
            "pass_rate": round(self.pass_rate, 4),
            "interval": {"lower": round(interval.lower, 4), "upper": round(interval.upper, 4)},
            "misses": self.misses,
            "required_passes": self.required_passes,
            "threshold": self.threshold,
            "quality_met": self.quality_met,
            "gate_passed": self.gate_passed,
            "status": self.status,
            "judge_agreement": self.judge_agreement,
            "split_verdicts": self.split_verdicts,
            "usage": self.usage.to_dict(),
            "latency": self.latency.to_dict(),
            "attempts": [run.to_dict() for run in self.runs],
        }


@dataclass(frozen=True)
class Resources:
    target: Target
    target_slots: asyncio.Semaphore
    judge_slots: asyncio.Semaphore


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

    def gate_for(self, scenario: Scenario) -> Gate:
        return self.config.run.gate(self.runs_for(scenario), scenario.misses, scenario.threshold)

    def run(self, scenario: Scenario, criterion: Criterion) -> ScenarioResult:
        return asyncio.run(self.run_async(scenario, criterion))

    async def run_async(self, scenario: Scenario, criterion: Criterion) -> ScenarioResult:
        async with self.resources() as resources:
            return await self.run_with(resources, scenario, criterion)

    @asynccontextmanager
    async def resources(self) -> AsyncIterator[Resources]:
        # One client and one provider serve every scenario, and the semaphores cap requests
        # to the target and to the judge across all of them.
        timeout = self.config.target.timeout_seconds
        async with httpx.AsyncClient(timeout=timeout) as client, self.provider:
            yield Resources(
                self.target_factory(client),
                asyncio.Semaphore(self.config.run.concurrency),
                asyncio.Semaphore(self.config.judge.concurrency),
            )

    async def run_with(
        self, resources: Resources, scenario: Scenario, criterion: Criterion
    ) -> ScenarioResult:
        gate = self.gate_for(scenario)
        runs = gate.runs
        try:
            # A task group cancels the remaining attempts when one raises, so a
            # configuration error stops the scenario instead of leaving tasks dangling.
            async with asyncio.TaskGroup() as group:
                tasks = [
                    group.create_task(self._attempt(resources, scenario, criterion, attempt))
                    for attempt in range(1, runs + 1)
                ]
        except* TargetConfigError as failures:
            raise failures.exceptions[0] from None
        records = [task.result() for task in tasks]
        return ScenarioResult(scenario, criterion, gate, records)

    async def _attempt(
        self,
        resources: Resources,
        scenario: Scenario,
        criterion: Criterion,
        attempt: int,
    ) -> RunRecord:
        record = RunRecord(attempt=attempt)
        history = [turn.model_dump() for turn in scenario.history]
        started = time.perf_counter()
        # Each turn is answered live and its reply becomes context for the next, so the
        # feature is driven through the conversation rather than handed a script.
        conversation: list[Turn] = list(scenario.history)
        for step, text in enumerate([*scenario.turns, scenario.message]):
            reply = await self._send(resources, record, text, conversation, attempt, step)
            if reply is None:
                record.elapsed_ms = (time.perf_counter() - started) * 1000
                return record
            if step < len(scenario.turns):
                exchange = [Turn(role="user", content=text), Turn(role="assistant", content=reply)]
                conversation.extend(exchange)
                record.transcript.extend(exchange)
            else:
                record.response = reply
        assert record.response is not None

        try:
            votes = await asyncio.gather(
                *(
                    self._vote(resources, criterion, scenario, history, record, vote)
                    for vote in range(1, self.config.judge.votes + 1)
                )
            )
        except (ProviderError, TransportFailure) as exc:
            record.judge_error = str(exc)
            record.elapsed_ms = (time.perf_counter() - started) * 1000
            return record
        record.votes = [verdict for verdict, _ in votes]
        record.fresh_votes = [verdict.usage for verdict, cached in votes if not cached]
        record.verdict = majority_verdict(record.votes)
        record.verdict_cached = all(cached for _, cached in votes)
        record.elapsed_ms = (time.perf_counter() - started) * 1000
        self.cache.append_log(
            "verdicts",
            {
                "scenario": scenario.id,
                "criterion": criterion.id,
                "attempt": attempt,
                "cached": record.verdict_cached,
                "temperature": self.provider.temperature,
                "votes": len(record.votes),
                "agreement": record.agreement,
                **record.verdict.to_dict(),
            },
        )
        return record

    async def _send(
        self,
        resources: Resources,
        record: RunRecord,
        text: str,
        conversation: list[Turn],
        attempt: int,
        step: int,
    ) -> str | None:
        # Replaying responses defeats repeated sampling, so it is opt in and every
        # replayed attempt is flagged in the record, the terminal and the report.
        replay = self.config.run.cache_responses
        target = resources.target
        key = Cache.key(
            "response",
            target.fingerprint(),
            text,
            [turn.model_dump() for turn in conversation],
            attempt,
            step,
        )
        cached = self.cache.get("responses", key) if replay else None
        if cached is not None:
            record.response_cached = True
            return str(cached["text"])
        async with resources.target_slots:
            try:
                response = await target.send(text, conversation)
            except TransportFailure as exc:
                turn = f"turn {step + 1}: " if step else ""
                record.error = f"{turn}{exc}"
                return None
        record.response_ms = (record.response_ms or 0.0) + response.elapsed_ms
        if replay:
            self.cache.put(
                "responses", key, {"text": response.text, "status_code": response.status_code}
            )
        return response.text

    async def _vote(
        self,
        resources: Resources,
        criterion: Criterion,
        scenario: Scenario,
        history: list[dict[str, str]],
        record: RunRecord,
        vote: int,
    ) -> tuple[Verdict, bool]:
        assert record.response is not None
        transcript = [turn.model_dump() for turn in record.transcript]
        key = Cache.key(
            "verdict",
            self.provider.fingerprint(),
            criterion.id,
            criterion.description,
            scenario.message,
            history,
            transcript,
            scenario.expected,
            record.response,
            vote,
        )
        # Votes exist to measure how much the judge wavers, and a cached majority would
        # freeze that figure, so verdicts are only cached when there is a single vote.
        use_cache = self.config.judge.votes == 1
        cached = self.cache.get("verdicts", key) if use_cache else None
        if cached is not None:
            return Verdict.from_dict(cached), True
        async with resources.judge_slots:
            verdict = await self.provider.judge(
                criterion, scenario, record.response, record.transcript
            )
        if use_cache:
            self.cache.put("verdicts", key, verdict.to_dict())
        return verdict, False


class Session:
    """Runs scenarios on one event loop in a background thread, so that scenarios submitted
    from sequential pytest items overlap and share the runner's resources."""

    def __init__(self, runner: Runner) -> None:
        self.runner = runner
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="juried", daemon=True)
        self._stack = AsyncExitStack()
        self._resources: Resources | None = None
        self._tasks: set[asyncio.Task[ScenarioResult]] = set()

    def __enter__(self) -> Session:
        self._thread.start()
        self._call(self._open()).result()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._call(self._shutdown()).result()
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join()
        self._loop.close()

    def submit(
        self, scenario: Scenario, criterion: Criterion
    ) -> concurrent.futures.Future[ScenarioResult]:
        return self._call(self._tracked(scenario, criterion))

    def _call(
        self, coroutine: Coroutine[Any, Any, ScenarioResult | None]
    ) -> concurrent.futures.Future[Any]:
        return asyncio.run_coroutine_threadsafe(coroutine, self._loop)

    async def _open(self) -> None:
        self._resources = await self._stack.enter_async_context(self.runner.resources())

    async def _tracked(self, scenario: Scenario, criterion: Criterion) -> ScenarioResult:
        task = asyncio.current_task()
        assert task is not None and self._resources is not None
        self._tasks.add(task)
        try:
            return await self.runner.run_with(self._resources, scenario, criterion)
        finally:
            self._tasks.discard(task)

    async def _shutdown(self) -> None:
        # Cancel whatever pytest never asked for, such as the items after a -x stop, before
        # the client and provider close.
        for task in list(self._tasks):
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        await self._stack.aclose()
