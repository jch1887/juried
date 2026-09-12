from __future__ import annotations

import concurrent.futures
import os
from collections.abc import Generator, Iterator
from pathlib import Path
from typing import Any

import pytest

from juried.cache import Cache
from juried.calibrate import CALIBRATION_REPORT
from juried.config import Config, ConfigError, find_config, load_config
from juried.criteria import CriteriaError, Criterion, load_criteria
from juried.judge import ProviderError, build_provider
from juried.pricing import Usage, describe_usage
from juried.report import ReportPaths, write_reports
from juried.runner import Runner, RunRecord, ScenarioResult, Session
from juried.scenarios import SCENARIO_SUFFIXES, Scenario, ScenarioError, load_scenario_file
from juried.stats import best_possible_lower_bound, describe_gate, required_passes
from juried.targets.http import TargetConfigError, expand_env

RESPONSE_EXCERPT = 1200


class JuriedState:
    def __init__(self, config: Config, config_path: Path, cache_enabled: bool) -> None:
        self.config = config
        self.config_path = config_path
        self.criteria: dict[str, Criterion] = {
            criterion.id: criterion for criterion in load_criteria(config.criteria_path)
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
                judge.provider, judge.model, judge.temperature, judge.max_tokens, judge.base_url
            )
            self._runner = Runner(self.config, provider, self.cache)
        return self._runner

    def gate_warning(self, runs: int, threshold: float) -> str | None:
        best = best_possible_lower_bound(runs)
        if best >= threshold:
            return None
        return (
            f"with {runs} runs the best possible lower bound is {best:.2f}, "
            f"below the threshold {threshold:.2f}, so the gate can never be upheld. "
            "Raise runs or lower the threshold."
        )


STATE = pytest.StashKey[JuriedState]()


class GateFailure(Exception):
    def __init__(self, result: ScenarioResult) -> None:
        super().__init__(result.scenario.id)
        self.result = result


def pytest_addoption(parser: pytest.Parser) -> None:
    group = parser.getgroup("juried", "juried acceptance scenarios")
    group.addoption("--juried-config", default=None, help="path to juried.toml")
    group.addoption("--juried-runs", type=int, default=None, help="override run.runs")
    group.addoption("--juried-threshold", type=float, default=None, help="override run.threshold")
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
    for kind in ("happy_path", "edge_case", "custom"):
        config.addinivalue_line("markers", f"{kind}: kind of juried scenario")

    explicit = config.getoption("--juried-config")
    path = Path(explicit) if explicit else find_config(Path(config.invocation_params.dir))
    if path is None:
        return
    try:
        juried_config = load_config(path)
        runs = config.getoption("--juried-runs")
        if runs is not None:
            juried_config.run.runs = runs
        threshold = config.getoption("--juried-threshold")
        if threshold is not None:
            juried_config.run.threshold = threshold
        if config.getoption("--juried-cache-responses"):
            juried_config.run.cache_responses = True
        # Resolve ${NAME} references now so a missing variable fails before any scenario runs.
        expand_env(juried_config.target.model_dump(), os.environ)
        state = JuriedState(juried_config, path, not config.getoption("--juried-no-cache"))
    except (ConfigError, CriteriaError, TargetConfigError) as exc:
        raise pytest.UsageError(f"juried: {exc}") from exc
    config.stash[STATE] = state


def pytest_report_header(config: pytest.Config) -> list[str]:
    state = config.stash.get(STATE, None)
    if state is None:
        return []
    run = state.config.run
    votes = f" ({state.config.judge.votes} votes)" if state.config.judge.votes > 1 else ""
    lines = [
        f"juried: config {state.config_path}, judge {state.config.judge.provider}/"
        f"{state.config.judge.model}{votes}, runs {run.runs}, threshold {run.threshold}, "
        f"cache {cache_mode(state)}, concurrency {run.concurrency} "
        f"target / {state.config.judge.concurrency} judge"
    ]
    if state.cache.enabled and state.config.run.cache_responses:
        lines.append(
            "juried: warning: responses are replayed from the cache where present, so "
            "repeated runs of a replayed scenario do not sample the feature"
        )
    warning = state.gate_warning(run.runs, run.threshold)
    if warning:
        lines.append(f"juried: warning: {warning}")
    else:
        lines.append(f"juried: {describe_gate(run.runs, run.threshold)}")
    return lines


def cache_mode(state: JuriedState) -> str:
    if not state.cache.enabled:
        return "off"
    return "verdicts and responses" if state.config.run.cache_responses else "verdicts only"


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
        if isinstance(excinfo.value, ProviderError | TargetConfigError):
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


def summarise(result: ScenarioResult) -> str:
    comparison = ">=" if result.quality_met else "<"
    if result.complete:
        text = (
            f"{result.passes}/{result.total} "
            f"(lower {result.interval.lower:.2f} {comparison} {result.threshold:.2f})"
        )
    else:
        text = (
            f"incomplete: {result.passes}/{result.judged} judged of {result.total} "
            f"(lower {result.interval.lower:.2f} {comparison} {result.threshold:.2f}) "
            f"with {describe_errors(result)}"
        )
    if result.responses_from_cache:
        text += f" [{result.responses_from_cache} response(s) replayed from cache]"
    if result.split_verdicts:
        text += f", judge split on {result.split_verdicts}"
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
    lines.append(f"    user: {scenario.message}")
    if record.error is not None:
        lines.append(f"    transport error: {record.error}")
        return lines
    lines.append(f"    response: {excerpt(record.response)}")
    if record.judge_error is not None:
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
    verdict = "met" if result.quality_met else "not met"
    lines.extend(
        [
            f"  criterion: {result.criterion.id} ({result.criterion.title})",
            f"  runs upheld: {result.passes}/{result.judged} judged = {result.pass_rate:.2f}",
            f"  lower bound: {interval.lower:.2f} (Wilson 95% interval {interval.lower:.2f} "
            f"to {interval.upper:.2f})",
            f"  threshold: {result.threshold:.2f}, gate upheld when the lower bound meets it "
            f"({verdict} on the judged attempts)",
            f"  transport errors: {result.transport_errors}",
            f"  judge errors: {result.judge_errors}",
        ]
    )
    if result.responses_from_cache:
        lines.append(
            f"  responses replayed from cache: {result.responses_from_cache} "
            "(these attempts did not sample the feature)"
        )
    warning = state.gate_warning(result.total, result.threshold)
    if warning:
        lines.append(f"  note: {warning}")
    else:
        needed = required_passes(result.total, result.threshold)
        lines.append(
            f"  note: {describe_gate(result.total, result.threshold)}; "
            f"{needed} of {result.total} runs had to pass and {result.passes} did"
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
