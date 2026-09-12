from juried.config import ProviderName
from juried.judge.anthropic import AnthropicProvider
from juried.judge.base import (
    LLMProvider,
    Provider,
    ProviderError,
    Verdict,
    agreement,
    majority_verdict,
)
from juried.judge.openai import OpenAIProvider
from juried.judge.stub import StubProvider


def build_provider(
    name: ProviderName,
    model: str,
    temperature: float | None = None,
    max_tokens: int = 2048,
    base_url: str | None = None,
) -> Provider:
    if name == "stub":
        return StubProvider(model)
    if name == "anthropic":
        return AnthropicProvider(model, temperature, max_tokens, base_url)
    if name == "openai":
        return OpenAIProvider(model, temperature, max_tokens, base_url)
    raise ProviderError(f"unknown provider {name!r}")


__all__ = [
    "AnthropicProvider",
    "LLMProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
    "StubProvider",
    "Verdict",
    "agreement",
    "build_provider",
    "majority_verdict",
]
