from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Generator, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from juried.adversarial import criteria_for
from juried.cache import Cache
from juried.calibrate import CALIBRATION_REPORT
from juried.checks import CheckError
from juried.config import Config, ConfigError, find_config, load_config
from juried.criteria import CriteriaError, Criterion
from juried.judge import ProviderError, build_provider
from juried.pricing import (
    TargetUsage,
    Usage,
    describe_run_cost,
    describe_target_usage,
    describe_usage,
    estimate_usd,
    prices_for,
    target_estimate_usd,
)
from juried.report import ReportPaths, write_reports
from juried.runner import Runner, RunRecord, ScenarioResult, Session
from juried.scenarios import SCENARIO_SUFFIXES, Scenario, ScenarioError, load_scenario_file
from juried.stats import Gate, GateError, deprecation_notice
from juried.targets.http import TargetConfigError, expand_env

RESPONSE_EXCERPT = 1200
XDIST_WORKERS = "PYTEST_XDIST_WORKER_COUNT"


@dataclass(frozen=True)
class Concurrency:
    target: int
    judge: int
    workers: int | None = None

    # The caps a single worker gets. Divided so that N workers in flight together add up
    # to what the file says, never below one each.
    def per_worker(self, scope: str) -> Concurrency:
        if self.workers is None or scope == "worker":
            return self
        return Concurrency(
            max(1, self.target // self.workers), max(1, self.judge // self.workers), self.workers
        )

    def describe(self, scope: str) -> str:
        text = f"concurrency {self.target} target / {self.judge} judge"
        if self.workers is None:
            return text
        effective = self.per_worker(scope)
        return (
            f"{text} ({effective.target} target / {effective.judge} judge per worker, "
            f"{self.workers} xdist workers, scope {scope})"
        )


def xdist_workers(environ: Mapping[str, str]) -> int | None:
    value = environ.get(XDIST_WORKERS, "").strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


class JuriedState:
    def __init__(
        self,
        config: Config,
        config_path: Path,
        cache_enabled: bool,
        workers: int | None = None,
    ) -> None:
        self.config = config
        self.config_path = config_path
        self.concurrency = Concurrency(config.run.concurrency, config.judge.concurrency, workers)
        effective = self.concurrency.per_worker(config.run.concurrency_scope)
        config.run.concurrency = effective.target
        config.judge.concurrency = effective.judge
        self.criteria: dict[str, Criterion] = {
            criterion.id: criterion for criterion in criteria_for(config)
        }
        self.cache = Cache(config.cache_path, enabled=cache_enabled)
        self.results: list[ScenarioResult] = []
        self.report_paths: ReportPaths | None = None
        self.session: Session | None = None
        self._runner: Runner | None = None

    @property
    def runner(self) -> Runner:
        if self._runner is None:
            judge = self.config.judge
            provider = build_provider(
                judge.provider,
                judge.model,
                judge.temperature,
                judge.max_tokens,
                judge.base_url,
                judge.api_key_env,
                judge.strict_quotes,
            )
            self._runner = Runner(self.config, provider, self.cache)
        return self._runner

    @property
    def gate(self) -> Gate:
        return self.config.run.gate()

    def gate_for(self, scenario: Scenario) -> Gate:
        runs = scenario.runs if scenario.runs is not None else self.config.run.runs
        return self.config.run.gate(runs, scenario.misses, scenario.threshold)


STATE = pytest.StashKey[JuriedState]()


class GateFailure(Exception):
    def __init__(self, result: ScenarioResult) -> None:
        super().__init__(result.scenario.id)
        self.result = result


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("juried", "juried acceptance scenarios")
    group.addoption("--juried-config", default=None, help="path to juried.toml")
    group.addoption("--juried-runs", type=int, default=None, help="override run.runs")
    group.addoption("--juried-misses", type=int, default=None, help="override run.misses")
    group.addoption(
        "--juried-threshold",
        type=float,
        default=None,
        help="override run.threshold (deprecated, derive misses from a threshold)",
    )
    group.addoption(
        "--juried-no-cache", action="store_true", help="ignore cached verdicts and responses"
    )
    group.addoption(
        "--juried-cache-responses",
        action="store_true",
        help="replay responses from the cache instead of sampling the feature",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "juried: scenario collected by juried")
    config.addinivalue_line("markers", "criterion(id): acceptance criterion of a juried scenario")
    for kind in ("happy_path", "edge_case", "custom", "adversarial"):
        config.addinivalue_line("markers", f"{kind}: kind of juried scenario")

    explicit = config.getoption("--juried-config")
    path = Path(explicit) if explicit else find_config(Path(config.invocation_params.dir))
    if path is None:
        return
    try:
        juried_config = load_config(path)
        juried_config.run = juried_config.run.with_overrides(
            config.getoption("--juried-runs"),
            config.getoption("--juried-misses"),
            config.getoption("--juried-threshold"),
        )
        if config.getoption("--juried-cache-responses"):
            juried_config.run.cache_responses = True
        # Resolve ${NAME} references now so a missing variable fails before any scenario runs.
        expand_env(juried_config.target.model_dump(), os.environ)
        state = JuriedState(
            juried_config,
            path,
            not config.getoption("--juried-no-cache"),
            xdist_workers(os.environ),
        )
    except (ConfigError, CriteriaError, TargetConfigError) as exc:
        raise pytest.UsageError(f"juried: {exc}") from exc
    config.stash[STATE] = state


def pytest_report_header(config: pytest.Config) -> list[str]:
    state = config.stash.get(STATE, None)
    if state is None:
        return []
    run = state.config.run
    gate = state.gate
    votes = f" ({state.config.judge.votes} votes)" if state.config.judge.votes > 1 else ""
    lines = [
        f"juried: config {state.config_path}, judge {state.config.judge.provider}/"
        f"{state.config.judge.model}{votes}, runs {gate.runs}, misses {gate.misses}, "
        f"cache {cache_mode(state)}, {state.concurrency.describe(run.concurrency_scope)}"
    ]
    notice = deprecation_notice(gate)
    if notice:
        lines.append(f"juried: {notice}")
    if state.cache.enabled and state.config.run.cache_responses:
        lines.append(
            "juried: warning: responses are replayed from the cache where present, so "
            "repeated runs of a replayed scenario do not sample the feature"
        )
    lines.append(f"juried: {gate.describe()}")
    return lines


def cache_mode(state: JuriedState) -> str:
    if not state.cache.enabled:
        return "off"
    responses = state.config.run.cache_responses
    if state.config.judge.votes > 1:
        return "responses only, verdicts re-judged (votes > 1)" if responses else "off (votes > 1)"
    return "verdicts and responses" if responses else "verdicts only"


def pytest_collect_file(file_path: Path, parent: pytest.Collector) -> pytest.Collector | None:
    state = parent.config.stash.get(STATE, None)
    if state is None or file_path.suffix not in SCENARIO_SUFFIXES:
        return None
    if not file_path.resolve().is_relative_to(state.config.scenarios_path.resolve()):
        return None
    return ScenarioFile.from_parent(parent, path=file_path)


class ScenarioFile(pytest.File):
    def collect(self) -> Iterator[pytest.Item]:
        state = self.config.stash[STATE]
        try:
            scenarios = load_scenario_file(self.path)
        except ScenarioError as exc:
            raise self.CollectError(str(exc)) from exc
        for scenario in scenarios:
            if scenario.criterion not in state.criteria:
                known = ", ".join(sorted(state.criteria))
                raise self.CollectError(
                    f"scenario {scenario.id!r} refers to unknown criterion "
                    f"{scenario.criterion!r}; known criteria: {known}"
                )
            try:
                state.gate_for(scenario)
            except GateError as exc:
                raise self.CollectError(f"scenario {scenario.id!r}: {exc}") from exc
            yield ScenarioItem.from_parent(self, name=scenario.id, scenario=scenario)


@pytest.hookimpl(wrapper=True)
def pytest_runtestloop(session: pytest.Session) -> Generator[None, object, object]:
    state = session.config.stash.get(STATE, None)
    items = [item for item in session.items if isinstance(item, ScenarioItem)]
    if state is None or not items:
        return (yield)
    # Every scenario starts now and runs alongside the others; each item then waits for its
    # own result in order, so pytest's output and -x behave as usual.
    with Session(state.runner) as run_session:
        state.session = run_session
        for item in items:
            item.future = run_session.submit(item.scenario, state.criteria[item.scenario.criterion])
        try:
            return (yield)
        finally:
            state.session = None


class ScenarioItem(pytest.Item):
    def __init__(self, *, scenario: Scenario, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.scenario = scenario
        self.result: ScenarioResult | None = None
        self.future: concurrent.futures.Future[ScenarioResult] | None = None
        self.add_marker("juried")
        self.add_marker(scenario.kind)
        self.add_marker(pytest.mark.criterion(scenario.criterion))

    def runtest(self) -> None:
        state = self.config.stash[STATE]
        criterion = state.criteria[self.scenario.criterion]
        if self.future is not None:
            self.result = self.future.result()
        else:
            self.result = state.runner.run(self.scenario, criterion)
        state.results.append(self.result)
        interval = self.result.interval
        self.user_properties.extend(
            [
                ("criterion", criterion.id),
                ("passes", self.result.passes),
                ("runs", self.result.total),
                ("pass_rate", f"{self.result.pass_rate:.4f}"),
                ("interval_lower", f"{interval.lower:.4f}"),
                ("interval_upper", f"{interval.upper:.4f}"),
                ("misses_tolerated", self.result.misses),
                ("passes_needed", self.result.required_passes),
                ("threshold", self.result.threshold),
                ("transport_errors", self.result.transport_errors),
            ]
        )
        if not self.result.gate_passed:
            raise GateFailure(self.result)

    def repr_failure(self, excinfo: pytest.ExceptionInfo[BaseException], style: Any = None) -> Any:
        if isinstance(excinfo.value, GateFailure):
            state = self.config.stash[STATE]
            return format_gate_failure(excinfo.value.result, state)
        if isinstance(excinfo.value, ProviderError | TargetConfigError | CheckError):
            return f"juried: {excinfo.value}"
        return super().repr_failure(excinfo, style)

    def reportinfo(self) -> tuple[Path, int | None, str]:
        return self.path, None, f"juried scenario: {self.scenario.name}"


def describe_errors(result: ScenarioResult) -> str:
    parts = []
    if result.transport_errors:
        parts.append(f"{result.transport_errors} transport error(s)")
    if result.judge_errors:
        parts.append(f"{result.judge_errors} judge error(s)")
    return " and ".join(parts)


def describe_interval(result: ScenarioResult) -> str:
    interval = result.interval
    return f"interval {interval.lower:.2f} to {interval.upper:.2f}"


def summarise(result: ScenarioResult) -> str:
    needs = f"needs {result.required_passes}"
    if result.complete:
        text = f"{result.passes}/{result.total} ({needs}, {describe_interval(result)})"
    else:
        text = (
            f"incomplete: {result.passes}/{result.judged} judged of {result.total} "
            f"({needs}, {describe_interval(result)}) with {describe_errors(result)}"
        )
    if result.responses_from_cache:
        text += f" [{result.responses_from_cache} response(s) replayed from cache]"
    if result.split_verdicts:
        text += f", judge split on {result.split_verdicts}"
    if result.checks_failed:
        text += f", {result.checks_failed} failed a check"
    return text


def excerpt(text: str | None) -> str:
    if text is None:
        return "(no response)"
    text = text.strip()
    if len(text) > RESPONSE_EXCERPT:
        return text[:RESPONSE_EXCERPT] + f"... ({len(text) - RESPONSE_EXCERPT} more characters)"
    return text


def format_run(record: RunRecord, scenario: Scenario) -> list[str]:
    lines = [f"  first failing run: attempt {record.attempt} ({record.label})"]
    lines.extend(f"    {turn.role}: {turn.content}" for turn in scenario.history)
    lines.extend(f"    {turn.role} (live): {excerpt(turn.content)}" for turn in record.transcript)
    lines.append(f"    user: {scenario.message}")
    if record.error is not None:
        lines.append(f"    transport error: {record.error}")
        return lines
    lines.append(f"    response: {excerpt(record.response)}")
    if record.checks:
        passed = sum(1 for outcome in record.checks if outcome.passed)
        summary = f"{passed} of {len(record.checks)} passed"
        lines.append(f"    checks: {summary}")
        lines.extend(f"      failed {outcome.describe()}" for outcome in record.failed_checks)
    if record.failed_by_checks:
        lines.append("    judge: not called, a check decided the attempt")
    elif record.judge_error is not None:
        lines.append(f"    judge error: {record.judge_error}")
    elif record.verdict is not None:
        votes = ""
        if len(record.votes) > 1:
            agreed = sum(1 for vote in record.votes if vote.passed == record.verdict.passed)
            votes = f", {agreed} of {len(record.votes)} votes"
        lines.append(f"    judge ({record.verdict.model}{votes}): {record.verdict.reason}")
    return lines


def format_gate_failure(result: ScenarioResult, state: JuriedState) -> str:
    interval = result.interval
    scenario = f"scenario {result.scenario.id!r} ({result.scenario.name})"
    if result.complete:
        lines = [f"juried gate failed for {scenario}"]
    else:
        lines = [
            f"juried could not complete {scenario}: {result.errors} of {result.total} "
            f"attempts ended in {describe_errors(result)}",
            "  these attempts are not counted in the pass rate; fix the endpoint or judge "
            "and run again",
        ]
    if result.judged:
        verdict = "met" if result.quality_met else "not met"
        missed = f"{result.fails} miss{'es' if result.fails != 1 else ''}"
        outcome = f"{missed} on the judged attempts, so the gate is {verdict}"
    else:
        outcome = "no attempt reached a verdict"
    lines.extend(
        [
            f"  criterion: {result.criterion.id} ({result.criterion.title})",
            f"  runs upheld: {result.passes}/{result.judged} judged = {result.pass_rate:.2f}",
            f"  gate: {result.gate.describe()}; {outcome}",
            f"  interval: Wilson 95% interval {interval.lower:.2f} to {interval.upper:.2f}",
            f"  transport errors: {result.transport_errors}",
            f"  judge errors: {result.judge_errors}",
        ]
    )
    if result.responses_from_cache:
        lines.append(
            f"  responses replayed from cache: {result.responses_from_cache} "
            "(these attempts did not sample the feature)"
        )
    failures = result.failures
    if failures:
        lines.extend(format_run(failures[0], result.scenario))
    return "\n".join(lines)


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    if call.when == "call" and isinstance(item, ScenarioItem) and item.result is not None:
        report.juried_summary = summarise(item.result)  # type: ignore[attr-defined]
    return report


def pytest_report_teststatus(
    report: pytest.CollectReport | pytest.TestReport, config: pytest.Config
) -> tuple[str, str, str] | None:
    summary = getattr(report, "juried_summary", None)
    if summary is None or report.when != "call":
        return None
    if report.passed:
        return "passed", ".", f"PASSED {summary}"
    if report.failed:
        return "failed", "F", f"FAILED {summary}"
    return None


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    state = session.config.stash.get(STATE, None)
    if state is None or not state.results:
        return
    state.report_paths = write_reports(
        state.config, list(state.criteria.values()), state.results, state.config.report_path
    )


def pytest_terminal_summary(terminalreporter: pytest.TerminalReporter) -> None:
    state = terminalreporter.config.stash.get(STATE, None)
    if state is None or not state.results:
        return
    passed = sum(1 for result in state.results if result.gate_passed)
    incomplete = sum(1 for result in state.results if not result.complete)
    transport = sum(result.transport_errors for result in state.results)
    judge = sum(result.judge_errors for result in state.results)
    terminalreporter.write_sep("-", "juried summary")
    terminalreporter.write_line(
        f"{len(state.results)} scenarios, {passed} upheld, "
        f"{len(state.results) - passed - incomplete} failed, {incomplete} incomplete, "
        f"{transport} transport errors, {judge} judge errors"
    )
    usage = sum((result.usage for result in state.results), Usage())
    judge_config = state.config.judge
    terminalreporter.write_line(
        f"judge usage: {describe_usage(usage, judge_config.model, judge_config.prices)}"
    )
    target = sum((result.target_usage for result in state.results), TargetUsage())
    target_config = state.config.target
    terminalreporter.write_line(
        "target usage: "
        + describe_target_usage(target, target_config.prices, target_config.cost_per_request)
    )
    run_cost = describe_run_cost(
        estimate_usd(usage, prices_for(judge_config.model, judge_config.prices)),
        target_estimate_usd(target, target_config.prices, target_config.cost_per_request),
    )
    if run_cost:
        terminalreporter.write_line(run_cost)
    replayed = sum(result.responses_from_cache for result in state.results)
    if replayed:
        total = sum(result.total for result in state.results)
        terminalreporter.write_line(
            f"juried: warning: {replayed} of {total} responses were replayed from "
            f"{state.cache.root / 'cache' / 'responses'} and did not sample the feature; "
            "run without --cache-responses to sample again",
            yellow=True,
            bold=True,
        )
    if (
        state.config.judge.provider != "stub"
        and not (state.config.report_path / CALIBRATION_REPORT).is_file()
    ):
        terminalreporter.write_line(
            f"juried: warning: no calibration report at "
            f"{state.config.report_path / CALIBRATION_REPORT}; these verdicts come from "
            f"{state.config.judge.provider}/{state.config.judge.model} and nothing has checked "
            "it against human labels. Label real responses under calibration/ and run "
            "'juried calibrate'",
            yellow=True,
        )
    if state.report_paths is not None:
        terminalreporter.write_line(f"report: {state.report_paths.html}")
        terminalreporter.write_line(f"json:   {state.report_paths.json}")
