import asyncio
import json
from collections.abc import Sequence
from pathlib import Path

import httpx
import pytest

from juried.cache import Cache
from juried.config import parse_config
from juried.criteria import Criterion
from juried.judge import Provider, StubProvider, Verdict
from juried.runner import Runner, ScenarioResult
from juried.scenarios import Scenario, Turn
from juried.targets.base import TargetResponse
from juried.transport import TransportFailure

CRITERION = Criterion("hours", "Opening hours", "States the hours.")


def scenario(**overrides: object) -> Scenario:
    data: dict[str, object] = {
        "id": "hours-happy",
        "criterion": "hours",
        "name": "Asks hours",
        "message": "When are you open?",
        "expected": 'Mentions "9am".',
    }
    data.update(overrides)
    return Scenario.model_validate(data)


class ScriptedTarget:
    def __init__(self, replies: Sequence[str | Exception]) -> None:
        self.replies = list(replies)
        self.calls = 0
        self.active = 0
        self.max_active = 0

    def fingerprint(self) -> str:
        return "scripted"

    async def send(self, message: str, history: Sequence[Turn]) -> TargetResponse:
        index = self.calls
        self.calls += 1
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        reply = self.replies[index % len(self.replies)]
        if isinstance(reply, Exception):
            raise reply
        return TargetResponse(reply, 200, 1.0)


class SplitProvider(StubProvider):
    """Fails every third verdict, so majorities and agreement can be checked."""

    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    async def judge(self, criterion: Criterion, scenario: Scenario, response_text: str) -> Verdict:
        self.calls += 1
        if self.calls % 3 == 0:
            return Verdict(False, "dissent", self.model, Verdict.now())
        return await super().judge(criterion, scenario, response_text)


def make_runner(
    tmp_path: Path,
    target: ScriptedTarget,
    runs: int = 10,
    concurrency: int = 4,
    cache: bool = True,
    cache_responses: bool = False,
    votes: int = 1,
    provider: Provider | None = None,
) -> Runner:
    config = parse_config(
        f'[target]\nurl = "http://unused/"\n[run]\nruns = {runs}\nconcurrency = {concurrency}\n'
        f'cache_dir = "{tmp_path / ".juried"}"\n'
        f"cache_responses = {'true' if cache_responses else 'false'}\n"
        f'[judge]\nprovider = "stub"\nvotes = {votes}\n',
        tmp_path,
        environ={},
    )
    return Runner(
        config,
        provider or StubProvider(),
        Cache(config.cache_path, enabled=cache),
        target_factory=lambda client: target,
    )


def test_repeated_runs_and_gate(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am to 5pm."] * 9 + ["Closed."])
    result = make_runner(tmp_path, target).run(scenario(), CRITERION)
    assert target.calls == 10
    assert result.total == 10
    assert result.passes == 9
    assert result.pass_rate == pytest.approx(0.9)
    assert result.interval.lower == pytest.approx(0.5958, abs=1e-4)
    assert not result.gate_passed
    assert len(result.failures) == 1
    assert result.failures[0].attempt == 10
    assert result.failures[0].reason == "response does not mention '9am'"
    assert all(run.verdict is not None and run.verdict.model == "stub" for run in result.runs)


def test_all_pass_meets_default_gate(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am to 5pm."])
    result = make_runner(tmp_path, target).run(scenario(), CRITERION)
    assert result.passes == 10
    assert result.gate_passed


def test_per_scenario_overrides(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am to 5pm.", "Closed."])
    result = make_runner(tmp_path, target).run(scenario(runs=4, threshold=0.1), CRITERION)
    assert result.total == 4
    assert result.passes == 2
    assert result.threshold == 0.1
    assert result.gate_passed


def test_transport_errors_are_distinct_from_judge_failures(tmp_path: Path) -> None:
    target = ScriptedTarget(
        ["Open 9am.", TransportFailure("HTTP 500 from http://bot", status_code=500), "Closed."]
    )
    result = make_runner(tmp_path, target, runs=3).run(scenario(), CRITERION)
    outcomes = [run.outcome for run in result.runs]
    assert outcomes == ["pass", "transport_error", "fail"]
    assert result.transport_errors == 1
    assert result.passes == 1
    assert result.runs[1].verdict is None
    assert "HTTP 500" in result.runs[1].reason
    data = result.to_dict()
    assert data["attempts"][1]["error"].startswith("HTTP 500")
    assert data["attempts"][1]["verdict"] is None
    assert data["attempts"][1]["response_ms"] is None
    assert data["latency"] == {"measured": 2, "mean_ms": 1.0, "max_ms": 1.0}


def test_concurrency_limit_respected(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am."])
    make_runner(tmp_path, target, runs=12, concurrency=3).run(scenario(), CRITERION)
    assert target.max_active == 3


def test_responses_are_sampled_afresh_by_default(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am.", "Open 9am.", "Closed."])
    runner = make_runner(tmp_path, target, runs=3)
    first = runner.run(scenario(), CRITERION)
    assert target.calls == 3
    assert first.responses_from_cache == 0
    second = runner.run(scenario(), CRITERION)
    assert target.calls == 6
    assert second.responses_from_cache == 0
    assert not any(run.response_cached for run in second.runs)
    assert all(run.verdict_cached for run in second.runs)
    assert all(run.response_ms == 1.0 for run in second.runs)
    assert not (tmp_path / ".juried" / "cache" / "responses").exists()
    assert second.to_dict()["responses_from_cache"] == 0


def test_response_replay_is_opt_in_and_flagged(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am.", "Open 9am.", "Closed."])
    runner = make_runner(tmp_path, target, runs=3, cache_responses=True)
    first = runner.run(scenario(), CRITERION)
    assert target.calls == 3
    assert not any(run.response_cached for run in first.runs)
    assert all(run.response_ms == 1.0 for run in first.runs)
    assert first.latency.to_dict() == {"measured": 3, "mean_ms": 1.0, "max_ms": 1.0}
    second = runner.run(scenario(), CRITERION)
    assert target.calls == 3
    assert all(run.response_cached and run.verdict_cached for run in second.runs)
    assert second.responses_from_cache == 3
    assert second.to_dict()["responses_from_cache"] == 3
    assert all(run.response_ms is None for run in second.runs)
    assert second.latency.measured == 0
    assert [run.outcome for run in second.runs] == [run.outcome for run in first.runs]

    log = (tmp_path / ".juried" / "verdicts.jsonl").read_text().splitlines()
    assert len(log) == 6
    entry = json.loads(log[-1])
    assert entry["model"] == "stub"
    assert entry["cached"] is True
    assert "judged_at" in entry


def test_cache_keys_include_attempt_and_content(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am."])
    runner = make_runner(tmp_path, target, runs=2, cache_responses=True)
    runner.run(scenario(), CRITERION)
    runner.run(scenario(message="Different question"), CRITERION)
    assert target.calls == 4
    runner.run(scenario(expected='Mentions "5pm".'), CRITERION)
    assert target.calls == 4
    responses = list((tmp_path / ".juried" / "cache" / "responses").glob("*.json"))
    verdicts = list((tmp_path / ".juried" / "cache" / "verdicts").glob("*.json"))
    assert len(responses) == 4
    assert len(verdicts) == 3


def test_cache_can_be_disabled(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am."])
    runner = make_runner(tmp_path, target, runs=2, cache=False, cache_responses=True)
    runner.run(scenario(), CRITERION)
    runner.run(scenario(), CRITERION)
    assert target.calls == 4
    assert not (tmp_path / ".juried" / "cache").exists()


def test_transport_errors_are_not_cached(tmp_path: Path) -> None:
    target = ScriptedTarget([TransportFailure("ConnectError"), "Open 9am."])
    runner = make_runner(tmp_path, target, runs=1, cache_responses=True)
    assert runner.run(scenario(), CRITERION).transport_errors == 1
    assert runner.run(scenario(), CRITERION).passes == 1


def test_http_target_end_to_end(tmp_path: Path, fake_bot_url: str) -> None:
    config = parse_config(
        f'[target]\nurl = "{fake_bot_url}/chat"\n[run]\nruns = 6\n'
        f'cache_dir = "{tmp_path / ".juried"}"\n[judge]\nprovider = "stub"\n',
        tmp_path,
        environ={},
    )
    runner = Runner(config, StubProvider(), Cache(config.cache_path), environ={})
    result = runner.run(
        scenario(message="How do refunds work?", expected='Mentions "14 days".'),
        CRITERION,
    )
    assert isinstance(result, ScenarioResult)
    assert result.total == 6
    assert 0 < result.passes < 6
    broken = parse_config(
        f'[target]\nurl = "{fake_bot_url}/broken"\nretries = 0\n[run]\nruns = 2\n'
        f'cache_dir = "{tmp_path / ".juried"}"\n',
        tmp_path,
        environ={},
    )
    result = Runner(broken, StubProvider(), Cache(broken.cache_path), environ={}).run(
        scenario(), CRITERION
    )
    assert result.transport_errors == 2
    assert not result.gate_passed


def test_single_vote_records_full_agreement(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am."])
    result = make_runner(tmp_path, target, runs=2).run(scenario(), CRITERION)
    assert all(len(run.votes) == 1 and run.agreement == 1.0 for run in result.runs)
    assert result.judge_agreement == 1.0
    assert result.split_verdicts == 0
    data = result.to_dict()
    assert data["judge_agreement"] == 1.0
    assert data["attempts"][0]["agreement"] == 1.0
    assert len(data["attempts"][0]["votes"]) == 1


def test_majority_of_votes_decides_and_splits_are_counted(tmp_path: Path) -> None:
    # Two different responses, so the second attempt's votes are not served from the cache.
    target = ScriptedTarget(["Open 9am.", "Open 9am!"])
    provider = SplitProvider()
    runner = make_runner(tmp_path, target, runs=2, concurrency=1, votes=3, provider=provider)
    result = runner.run(scenario(), CRITERION)
    assert provider.calls == 6
    assert result.passes == 2
    assert [len(run.votes) for run in result.runs] == [3, 3]
    assert [run.agreement for run in result.runs] == [pytest.approx(2 / 3), pytest.approx(2 / 3)]
    assert result.split_verdicts == 2
    assert result.judge_agreement == pytest.approx(2 / 3)
    assert all(run.verdict is not None and run.verdict.reason != "dissent" for run in result.runs)

    log = (tmp_path / ".juried" / "verdicts.jsonl").read_text().splitlines()
    assert json.loads(log[-1])["votes"] == 3
    assert json.loads(log[-1])["agreement"] == pytest.approx(2 / 3)
    verdicts = list((tmp_path / ".juried" / "cache" / "verdicts").glob("*.json"))
    assert len(verdicts) == 6

    again = runner.run(scenario(), CRITERION)
    assert provider.calls == 6
    assert all(run.verdict_cached for run in again.runs)
    assert again.split_verdicts == 2


def test_majority_can_fail_a_passing_stub(tmp_path: Path) -> None:
    class Dissenter(StubProvider):
        async def judge(
            self, criterion: Criterion, scenario: Scenario, response_text: str
        ) -> Verdict:
            return Verdict(False, "always no", self.model, Verdict.now())

    target = ScriptedTarget(["Open 9am."])
    runner = make_runner(tmp_path, target, runs=1, votes=3, provider=Dissenter())
    result = runner.run(scenario(), CRITERION)
    assert result.passes == 0
    assert result.runs[0].agreement == 1.0
    assert result.runs[0].reason == "always no"


def test_result_dict_shape(tmp_path: Path) -> None:
    target = ScriptedTarget(["Open 9am."])
    result = make_runner(tmp_path, target, runs=2).run(scenario(tags=["smoke"]), CRITERION)
    data = result.to_dict()
    assert data["id"] == "hours-happy"
    assert data["criterion"] == "hours"
    assert data["runs"] == 2
    assert data["passes"] == 2
    assert data["tags"] == ["smoke"]
    assert data["interval"] == {"lower": pytest.approx(0.3424, abs=1e-4), "upper": 1.0}
    assert data["gate_passed"] is False
    assert data["attempts"][0]["verdict"]["passed"] is True
    assert isinstance(httpx.AsyncClient, type)
