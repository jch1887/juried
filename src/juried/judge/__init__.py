from collections.abc import Callable
from importlib.metadata import EntryPoint, entry_points
from typing import cast

from juried.config import ConfigError
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
from juried.pricing import Usage

BUILTIN_PROVIDERS = ("anthropic", "openai", "stub")
# Entry point group a third party package registers a Provider subclass under.
PLUGIN_GROUP = "juried.providers"


def build_provider(
    name: str,
    model: str,
    temperature: float | None = None,
    max_tokens: int = 2048,
    base_url: str | None = None,
    api_key_env: str | None = None,
    strict_quotes: bool = False,
) -> Provider:
    if name == "stub":
        return StubProvider(model, strict_quotes)
    if name == "anthropic":
        return AnthropicProvider(model, temperature, max_tokens, base_url, api_key_env)
    if name == "openai":
        return OpenAIProvider(model, temperature, max_tokens, base_url, api_key_env)
    plugin = plugin_provider_class(name)
    if plugin is None:
        raise ProviderError(f"unknown provider {name!r}")
    provider = plugin(model, temperature, max_tokens, base_url, api_key_env)
    if not getattr(provider, "name", None):
        provider.name = name
    return provider


def plugin_provider_class(name: str) -> Callable[..., Provider] | None:
    for entry in entry_points(group=PLUGIN_GROUP):
        if entry.name == name:
            return load_provider_class(name, entry)
    return None


def load_provider_class(name: str, entry: EntryPoint) -> Callable[..., Provider]:
    try:
        loaded = entry.load()
    except Exception as exc:
        raise ConfigError(
            f"provider {name!r} could not be loaded from {entry.value}: {exc}"
        ) from exc
    if isinstance(loaded, type):
        label = f"{loaded.__module__}.{loaded.__qualname__}"
    else:
        label = repr(loaded)
    if not isinstance(loaded, type) or not issubclass(loaded, Provider):
        raise ConfigError(
            f"provider {name!r} loads {label}, which does not subclass juried.judge.Provider"
        )
    missing = sorted(getattr(loaded, "__abstractmethods__", ()))
    if missing:
        raise ConfigError(
            f"provider {name!r} loads {label}, which does not define {', '.join(missing)}"
        )
    return cast(Callable[..., Provider], loaded)


__all__ = [
    "BUILTIN_PROVIDERS",
    "PLUGIN_GROUP",
    "AnthropicProvider",
    "LLMProvider",
    "OpenAIProvider",
    "Provider",
    "ProviderError",
    "StubProvider",
    "Usage",
    "Verdict",
    "agreement",
    "build_provider",
    "majority_verdict",
]
