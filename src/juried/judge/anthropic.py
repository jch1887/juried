from __future__ import annotations

from typing import Any

from juried.judge.base import LLMProvider, ProviderError, parse_json_object, usage_from
from juried.pricing import Usage

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"


class AnthropicProvider(LLMProvider):
    name = "anthropic"
    default_api_key_env = "ANTHROPIC_API_KEY"

    async def complete_json(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> tuple[dict[str, Any], Usage]:
        api_key = self.api_key()
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        response = await self.post_json(
            f"{(self.base_url or DEFAULT_BASE_URL).rstrip('/')}/v1/messages",
            {
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            body,
        )
        payload = response.json()
        # Cache reads and writes are billed at different rates, but both are input tokens
        # for the estimate, which errs on the side of overstating the spend.
        usage = usage_from(
            payload,
            ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"),
            "output_tokens",
        )
        if payload.get("stop_reason") == "refusal":
            raise ProviderError(f"{self.model} refused the request: {payload.get('stop_details')}")
        if payload.get("stop_reason") == "max_tokens":
            raise ProviderError(
                f"{self.model} ran out of output tokens before answering; raise max_tokens "
                f"under [judge] in juried.toml (currently {max_tokens})"
            )
        text = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        return parse_json_object(text), usage
