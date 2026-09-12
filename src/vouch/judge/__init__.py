from vouch.config import ProviderName
from vouch.judge.anthropic import AnthropicProvider
from vouch.judge.base import LLMProvider, Provider, ProviderError, Verdict
from vouch.judge.openai import OpenAIProvider
from vouch.judge.stub import StubProvider


def build_provider(
    name: ProviderName,
    model: str,
    temperature: float = 0.0,
    max_tokens: int = 512,
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
    "build_provider",
]
