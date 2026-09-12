"""Contract tests against the real provider APIs.

juried sends hand written requests to both providers, so the unit tests can only check what
it sends, never that the API still accepts it. These tests send a tiny judge and generate
request to each provider, check that the verdict and the drafts parse and that usage token
counts come back, and are the only thing that notices a field rename upstream.

They are deselected by default. Run them with:

    JURIED_LIVE=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... pytest -m live

A provider whose key is not set is skipped rather than failed.
"""

from __future__ import annotations

import asyncio
import os
from typing import cast

import pytest

from juried.config import ProviderName
from juried.criteria import Criterion
from juried.judge import LLMProvider, Usage, build_provider
from juried.scenarios import Scenario, ScenarioDraft

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.environ.get("JURIED_LIVE") != "1", reason="set JURIED_LIVE=1"),
]

CRITERION = Criterion(
    "opening-hours",
    "Opening hours",
    "The assistant tells customers the shop is open Monday to Friday, 9am to 5pm.",
)
SCENARIO = Scenario(
    id="opening-hours-asks",
    criterion="opening-hours",
    name="Asks the hours",
    message="What time do you open?",
    expected='Gives the opening time "9am" and the closing time.',
)
PASSING = "We are open Monday to Friday, 9am to 5pm."
FAILING = "We are open whenever the owner feels like it, usually around noon."

PROVIDERS = [
    ("anthropic", "ANTHROPIC_API_KEY", "JURIED_LIVE_ANTHROPIC_MODEL", "claude-haiku-4-5"),
    ("openai", "OPENAI_API_KEY", "JURIED_LIVE_OPENAI_MODEL", "gpt-4.1-mini"),
]


def live_provider(name: str, key: str, model_variable: str, default_model: str) -> LLMProvider:
    if not os.environ.get(key):
        pytest.skip(f"{key} is not set")
    model = os.environ.get(model_variable, default_model)
    provider = build_provider(cast(ProviderName, name), model, None, 512)
    assert isinstance(provider, LLMProvider)
    return provider


@pytest.mark.parametrize(("name", "key", "model_variable", "default_model"), PROVIDERS)
def test_judge_request_is_accepted_and_verdicts_are_sane(
    name: str, key: str, model_variable: str, default_model: str
) -> None:
    provider = live_provider(name, key, model_variable, default_model)

    async def go() -> tuple[bool, bool]:
        async with provider:
            good = await provider.judge(CRITERION, SCENARIO, PASSING)
            bad = await provider.judge(CRITERION, SCENARIO, FAILING)
        assert good.reason and bad.reason
        assert good.model == provider.model
        for verdict in (good, bad):
            assert verdict.usage.calls == 1
            assert verdict.usage.input_tokens > 0, "no input token count came back"
            assert verdict.usage.output_tokens > 0, "no output token count came back"
        assert provider.usage_total.calls == 2
        assert provider.usage_total.input_tokens == good.usage.input_tokens + bad.usage.input_tokens
        return good.passed, bad.passed

    assert asyncio.run(go()) == (True, False)


@pytest.mark.parametrize(("name", "key", "model_variable", "default_model"), PROVIDERS)
def test_generate_request_is_accepted(
    name: str, key: str, model_variable: str, default_model: str
) -> None:
    provider = live_provider(name, key, model_variable, default_model)

    async def go() -> int:
        async with provider:
            drafts = await provider.generate(CRITERION, 2)
        assert all(isinstance(draft, ScenarioDraft) for draft in drafts)
        assert all(draft.name and draft.message and draft.expected for draft in drafts)
        assert {draft.kind for draft in drafts} <= {"happy_path", "edge_case"}
        assert all(turn.role in ("user", "assistant") for d in drafts for turn in d.history)
        usage: Usage = provider.usage_total
        assert usage.calls == 1
        assert usage.input_tokens > 0 and usage.output_tokens > 0, "no token counts came back"
        return len(drafts)

    assert asyncio.run(go()) >= 1
