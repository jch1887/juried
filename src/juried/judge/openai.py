from __future__ import annotations

import os
from typing import Any

from juried.judge.base import (
    LLMProvider,
    ProviderError,
    parse_json_object,
    require_env,
    usage_from,
)
from juried.pricing import Usage

DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIProvider(LLMProvider):
    name = "openai"

    async def complete_json(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> tuple[dict[str, Any], Usage]:
        api_key = require_env(dict(os.environ), "OPENAI_API_KEY", self.name)
        body: dict[str, Any] = {
            "model": self.model,
            "max_completion_tokens": max_tokens,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "juried_output", "strict": True, "schema": schema},
            },
        }
        if self.temperature is not None:
            body["temperature"] = self.temperature
        response = await self.post_json(
            f"{(self.base_url or DEFAULT_BASE_URL).rstrip('/')}/chat/completions",
            {"Authorization": f"Bearer {api_key}", "content-type": "application/json"},
            body,
        )
        payload = response.json()
        usage = usage_from(payload, ("prompt_tokens",), "completion_tokens")
        choices = payload.get("choices") or []
        if not choices:
            raise ProviderError(f"{self.model} returned no choices: {payload!r}")
        message = choices[0].get("message", {})
        if message.get("refusal"):
            raise ProviderError(f"{self.model} refused the request: {message['refusal']}")
        return parse_json_object(str(message.get("content", ""))), usage
