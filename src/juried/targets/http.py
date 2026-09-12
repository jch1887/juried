from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from juried.config import TargetConfig
from juried.scenarios import Turn
from juried.targets.base import TargetResponse
from juried.transport import TransportFailure, request_json

MESSAGE_PLACEHOLDER = "{{message}}"
HISTORY_PLACEHOLDER = "{{history}}"
ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class TargetConfigError(Exception):
    pass


def render_body(template: Any, message: str, history: Sequence[Turn]) -> Any:
    if isinstance(template, str):
        if template == HISTORY_PLACEHOLDER:
            return [turn.model_dump() for turn in history]
        return template.replace(MESSAGE_PLACEHOLDER, message)
    if isinstance(template, dict):
        return {key: render_body(value, message, history) for key, value in template.items()}
    if isinstance(template, list):
        return [render_body(item, message, history) for item in template]
    return template


def extract_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if isinstance(current, list) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise TargetConfigError(f"response_path {path!r}: index {index} out of range")
            current = current[index]
        elif isinstance(current, dict) and part in current:
            current = current[part]
        else:
            raise TargetConfigError(
                f"response_path {path!r}: key {part!r} not found in {json.dumps(data)[:200]}"
            )
    return current


def expand_env(headers: Mapping[str, str], environ: Mapping[str, str]) -> dict[str, str]:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environ:
            raise TargetConfigError(f"header refers to unset environment variable {name}")
        return environ[name]

    return {key: ENV_REFERENCE.sub(replace, value) for key, value in headers.items()}


class HttpTarget:
    def __init__(
        self,
        config: TargetConfig,
        client: httpx.AsyncClient,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        self.headers = expand_env(config.headers, os.environ if environ is None else environ)

    def fingerprint(self) -> str:
        return json.dumps(
            {
                "url": self.config.url,
                "method": self.config.method,
                "body": self.config.body,
                "response_path": self.config.response_path,
            },
            sort_keys=True,
        )

    async def send(self, message: str, history: Sequence[Turn]) -> TargetResponse:
        body = render_body(self.config.body, message, history)
        started = time.perf_counter()
        response = await request_json(
            self.client,
            self.config.method,
            self.config.url,
            headers=self.headers,
            body=body,
            retries=self.config.retries,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        try:
            payload = response.json()
        except ValueError as exc:
            raise TransportFailure(
                f"response from {self.config.url} is not JSON: {response.text[:200]!r}",
                status_code=response.status_code,
            ) from exc
        text = extract_path(payload, self.config.response_path)
        if not isinstance(text, str):
            raise TargetConfigError(
                f"response_path {self.config.response_path!r} did not select a string: {text!r}"
            )
        return TargetResponse(text, response.status_code, elapsed_ms, payload)
