import asyncio
import json
from typing import Any

import httpx
import pytest

from vouch.criteria import Criterion
from vouch.judge import ProviderError, StubProvider, build_provider
from vouch.judge.anthropic import AnthropicProvider, supports_temperature
from vouch.judge.base import parse_json_object
from vouch.judge.openai import OpenAIProvider
from vouch.scenarios import Scenario

CRITERION = Criterion("hours", "Opening hours", "States the hours, 9am to 5pm.")
SCENARIO = Scenario(
    id="hours-happy",
    criterion="hours",
    name="Asks hours",
    message="When are you open?",
    expected='Mentions "9am" and "5pm".',
)


def run(coro: Any) -> Any:
    return asyncio.run(coro)


def test_stub_judge_checks_quoted_phrases() -> None:
    stub = StubProvider()
    good = run(stub.judge(CRITERION, SCENARIO, "Open 9AM to 5pm weekdays."))
    assert good.passed
    assert good.model == "stub"
    assert good.judged_at.endswith("+00:00")
    bad = run(stub.judge(CRITERION, SCENARIO, "Open 9am until late."))
    assert not bad.passed
    assert "5pm" in bad.reason
    empty = run(stub.judge(CRITERION, SCENARIO, "   "))
    assert not empty.passed


def test_stub_generate_is_deterministic() -> None:
    stub = StubProvider()
    drafts = run(stub.generate(CRITERION, 4))
    assert [d.kind for d in drafts] == ["happy_path", "edge_case"]
    assert drafts[0].message == "Can you tell me about opening hours?"
    wrapped = Criterion("c", "Title", "First line\nsecond line.")
    assert run(stub.generate(wrapped, 1))[0].expected == "First line second line."
    assert drafts == run(stub.generate(CRITERION, 2))
    assert len(run(stub.generate(CRITERION, 1))) == 1


def test_parse_json_object_tolerates_surrounding_text() -> None:
    assert parse_json_object('Sure: {"pass": true, "reason": "ok"} done') == {
        "pass": True,
        "reason": "ok",
    }
    with pytest.raises(ProviderError, match="JSON object"):
        parse_json_object("[1, 2]")


def test_build_provider() -> None:
    assert isinstance(build_provider("stub", "x"), StubProvider)
    assert isinstance(build_provider("anthropic", "claude-sonnet-5"), AnthropicProvider)
    assert isinstance(build_provider("openai", "gpt-4.1"), OpenAIProvider)


def test_supports_temperature() -> None:
    assert supports_temperature("claude-sonnet-4-6")
    assert supports_temperature("claude-haiku-4-5")
    assert not supports_temperature("claude-sonnet-5")
    assert not supports_temperature("claude-opus-5")


def mock_client(handler: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def test_anthropic_request_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-123")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["headers"] = dict(request.headers)
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"pass": false, "reason": "no 5pm"}'}],
            },
        )

    provider = AnthropicProvider("claude-sonnet-4-6", 0.0, 256)
    provider._client = mock_client(handler)
    verdict = run(provider.judge(CRITERION, SCENARIO, "Open 9am."))
    assert not verdict.passed
    assert verdict.reason == "no 5pm"
    assert verdict.model == "claude-sonnet-4-6"
    assert seen["url"] == "https://api.anthropic.com/v1/messages"
    assert seen["headers"]["x-api-key"] == "key-123"
    assert seen["headers"]["anthropic-version"] == "2023-06-01"
    body = seen["body"]
    assert body["model"] == "claude-sonnet-4-6"
    assert body["temperature"] == 0.0
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["messages"][0]["role"] == "user"
    assert "Open 9am." in body["messages"][0]["content"]
    assert "acceptance test" in body["system"]


def test_anthropic_omits_temperature_for_models_without_sampling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-123")
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"content": [{"type": "text", "text": '{"pass": true, "reason": "fine"}'}]},
        )

    provider = AnthropicProvider("claude-sonnet-5", 0.0, 256, base_url="http://proxy.test/")
    provider._client = mock_client(handler)
    assert run(provider.judge(CRITERION, SCENARIO, "9am to 5pm")).passed
    assert "temperature" not in seen["body"]


def test_anthropic_requires_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    provider = AnthropicProvider("claude-sonnet-5", 0.0, 256)
    with pytest.raises(ProviderError, match="ANTHROPIC_API_KEY"):
        run(provider.judge(CRITERION, SCENARIO, "x"))


def test_anthropic_refusal_is_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "key-123")
    provider = AnthropicProvider("claude-sonnet-5", 0.0, 256)
    provider._client = mock_client(
        lambda request: httpx.Response(200, json={"stop_reason": "refusal", "content": []})
    )
    with pytest.raises(ProviderError, match="refused"):
        run(provider.judge(CRITERION, SCENARIO, "x"))


def test_openai_request_shape_and_generate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    seen: dict[str, Any] = {}
    generated = {
        "scenarios": [
            {"name": "Asks", "kind": "happy_path", "message": "hours?", "expected": "hours"},
            {"name": "Typo", "kind": "edge_case", "message": "hourz", "expected": "hours"},
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["body"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(generated)}}]}
        )

    provider = OpenAIProvider("gpt-4.1", 0.0, 512)
    provider._client = mock_client(handler)
    drafts = run(provider.generate(CRITERION, 2))
    assert [d.name for d in drafts] == ["Asks", "Typo"]
    assert seen["url"] == "https://api.openai.com/v1/chat/completions"
    assert seen["auth"] == "Bearer sk-test"
    body = seen["body"]
    assert body["temperature"] == 0.0
    assert body["response_format"]["json_schema"]["strict"] is True
    assert body["messages"][0]["role"] == "system"
    assert body["max_completion_tokens"] == 4096


def test_openai_bad_generation_is_provider_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    provider = OpenAIProvider("gpt-4.1", 0.0, 512)
    provider._client = mock_client(
        lambda request: httpx.Response(
            200,
            json={"choices": [{"message": {"content": '{"scenarios": [{"name": "x"}]}'}}]},
        )
    )
    with pytest.raises(ProviderError, match="invalid scenario"):
        run(provider.generate(CRITERION, 2))
