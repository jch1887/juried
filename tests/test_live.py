"""Contract tests against the real provider APIs.

juried sends hand written requests to both providers, so the unit tests can only check what
it sends, never that the API still accepts it. These tests send a tiny judge and generate
request to each provider, check that the verdict and the drafts parse and that usage token
counts come back, and are the only thing that notices a field rename upstream.

They are deselected by default. Run them with:

    JURIED_LIVE=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... pytest -m live

A provider whose key is not set is skipped rather than failed. One more test sends a judge
request through the openai provider to a local Ollama, the stand in for every OpenAI
compatible endpoint; it is skipped, with the reason in the log, when none is running at
JURIED_LIVE_OLLAMA_BASE_URL (default http://127.0.0.1:11434/v1) or the model named by
JURIED_LIVE_OLLAMA_MODEL (default llama3.2) is not pulled.
"""

from __future__ import annotations

import asyncio
import os
from typing import cast

import httpx
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
OLLAMA_BASE_URL = os.environ.get("JURIED_LIVE_OLLAMA_BASE_URL") or "http://127.0.0.1:11434/v1"
OLLAMA_MODEL = os.environ.get("JURIED_LIVE_OLLAMA_MODEL") or "llama3.2"
OLLAMA_KEY_ENV = "JURIED_LIVE_OLLAMA_KEY"


def live_provider(name: str, key: str, model_variable: str, default_model: str) -> LLMProvider:
    if not os.environ.get(key):
        pytest.skip(f"{key} is not set")
    # The workflow exports the override variables even when they are unset, so an empty
    # value means "use the default" rather than "send an empty model name".
    model = os.environ.get(model_variable) or default_model
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


def ollama_provider(monkeypatch: pytest.MonkeyPatch) -> LLMProvider:
    root = OLLAMA_BASE_URL.rstrip("/").removesuffix("/v1")
    try:
        tags = httpx.get(f"{root}/api/tags", timeout=3.0)
        tags.raise_for_status()
    except httpx.HTTPError:
        pytest.skip(f"no Ollama at {root}: the OpenAI compatible endpoint test did not run")
    names = [str(entry.get("name", "")) for entry in tags.json().get("models", [])]
    if not any(name == OLLAMA_MODEL or name.startswith(f"{OLLAMA_MODEL}:") for name in names):
        pytest.skip(f"Ollama at {root} has no model {OLLAMA_MODEL}; pull it or set the variable")
    # Ollama ignores the key, but the variable must hold something for juried to send.
    monkeypatch.setenv(OLLAMA_KEY_ENV, os.environ.get(OLLAMA_KEY_ENV) or "ollama")
    provider = build_provider("openai", OLLAMA_MODEL, None, 512, OLLAMA_BASE_URL, OLLAMA_KEY_ENV)
    assert isinstance(provider, LLMProvider)
    return provider


def test_openai_compatible_endpoint_accepts_a_judge_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = ollama_provider(monkeypatch)

    async def go() -> None:
        async with provider:
            verdict = await provider.judge(CRITERION, SCENARIO, PASSING)
        # A small local model may well misjudge, so only the contract is checked: the
        # request was accepted, a verdict parsed, and token counts came back.
        assert verdict.reason
        assert verdict.model == OLLAMA_MODEL
        assert verdict.usage.calls == 1
        assert verdict.usage.input_tokens > 0, "no input token count came back"
        assert verdict.usage.output_tokens > 0, "no output token count came back"

    asyncio.run(go())
