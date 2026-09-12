from __future__ import annotations

import os
from typing import Any

from juried.judge.base import LLMProvider, ProviderError, parse_json_object, require_env
from juried.transport import request_json

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
SAMPLING_MODEL_MARKERS = ("-4-6", "-4-5", "-4-1", "-4-2025", "-3-")


def supports_temperature(model: str) -> bool:
    return any(marker in model for marker in SAMPLING_MODEL_MARKERS)


class AnthropicProvider(LLMProvider):
    name = "anthropic"

    async def complete_json(
        self, system: str, user: str, schema: dict[str, Any], max_tokens: int
    ) -> dict[str, Any]:
        api_key = require_env(dict(os.environ), "ANTHROPIC_API_KEY", self.name)
        body: dict[str, Any] = {
            "model": self.model,
            "max_tokens": max_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "output_config": {"format": {"type": "json_schema", "schema": schema}},
        }
        if supports_temperature(self.model):
            body["temperature"] = self.temperature
        response = await request_json(
            self.client,
            "POST",
            f"{(self.base_url or DEFAULT_BASE_URL).rstrip('/')}/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
            },
            body=body,
            retries=3,
        )
        payload = response.json()
        if payload.get("stop_reason") == "refusal":
            raise ProviderError(f"{self.model} refused the request: {payload.get('stop_details')}")
        text = "".join(
            block.get("text", "")
            for block in payload.get("content", [])
            if block.get("type") == "text"
        )
        return parse_json_object(text)
