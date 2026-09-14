from __future__ import annotations

import json
import os
import re
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from juried.config import TargetConfig
from juried.scenarios import Turn
from juried.targets.base import TargetResponse
from juried.transport import TransportFailure, request_json, stream_response

MESSAGE_PLACEHOLDER = "{{message}}"
HISTORY_PLACEHOLDER = "{{history}}"
ENV_REFERENCE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


class TargetConfigError(Exception):
    pass


def render_body(template: Any, message: str, history: Sequence[Turn]) -> Any:
    # A value that is exactly a placeholder becomes the value itself, keeping its type; a
    # placeholder inside longer text is replaced by its text, the history as JSON.
    turns = [turn.model_dump() for turn in history]
    if isinstance(template, str):
        if template == MESSAGE_PLACEHOLDER:
            return message
        if template == HISTORY_PLACEHOLDER:
            return turns
        return template.replace(MESSAGE_PLACEHOLDER, message).replace(
            HISTORY_PLACEHOLDER, json.dumps(turns, ensure_ascii=False)
        )
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


def extract_count(data: Any, path: str, key: str) -> int:
    try:
        value = extract_path(data, path)
    except TargetConfigError as exc:
        raise TargetConfigError(str(exc).replace("response_path", key, 1)) from None
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TargetConfigError(f"{key} {path!r} did not select a number: {value!r}")
    return int(value)


def expand_env(value: Any, environ: Mapping[str, str]) -> Any:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in environ:
            raise TargetConfigError(f"[target] refers to unset environment variable {name}")
        return environ[name]

    if isinstance(value, str):
        return ENV_REFERENCE.sub(replace, value)
    if isinstance(value, dict):
        return {key: expand_env(item, environ) for key, item in value.items()}
    if isinstance(value, list):
        return [expand_env(item, environ) for item in value]
    return value


class HttpTarget:
    def __init__(
        self,
        config: TargetConfig,
        client: httpx.AsyncClient,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self.config = config
        self.client = client
        # Secrets are expanded here and never reach the fingerprint, which hashes the template.
        env = os.environ if environ is None else environ
        self.url: str = expand_env(config.url, env)
        self.headers: dict[str, str] = expand_env(config.headers, env)
        self.body_template = expand_env(config.body, env)

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
        body = render_body(self.body_template, message, history)
        if self.config.stream:
            return await self._send_stream(body)
        started = time.perf_counter()
        response = await request_json(
            self.client,
            self.config.method,
            self.url,
            headers=self.headers,
            body=body,
            retries=self.config.retries,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000
        try:
            payload = response.json()
        except ValueError as exc:
            raise TransportFailure(
                f"response from {self.url} is not JSON: {response.text[:200]!r}",
                status_code=response.status_code,
            ) from exc
        text = extract_path(payload, self.config.response_path)
        if not isinstance(text, str):
            raise TargetConfigError(
                f"response_path {self.config.response_path!r} did not select a string: {text!r}"
            )
        input_tokens = output_tokens = None
        if self.config.usage_paths is not None:
            input_path, output_path = self.config.usage_paths
            input_tokens = extract_count(payload, input_path, "usage_input_path")
            output_tokens = extract_count(payload, output_path, "usage_output_path")
        return TargetResponse(
            text,
            response.status_code,
            elapsed_ms,
            payload,
            len(response.content),
            input_tokens,
            output_tokens,
        )

    # Events are read as they arrive; the text at stream_path in each is appended, the
    # first one stamps the time to first token, and token counts are taken from whichever
    # event carries them, since providers put usage on the last one.
    async def _send_stream(self, body: Any) -> TargetResponse:
        assert self.config.stream_path is not None
        started = time.perf_counter()
        pieces: list[str] = []
        events = 0
        total_bytes = 0
        first_token_ms: float | None = None
        input_tokens = output_tokens = None
        last_event: Any = None
        async with stream_response(
            self.client,
            self.config.method,
            self.url,
            headers=self.headers,
            body=body,
            retries=self.config.retries,
        ) as response:
            status_code = response.status_code
            async for event in iter_events(response, self.config.stream_format, self.url):
                events += 1
                total_bytes += event.size
                last_event = event.data
                text = delta_text(event.data, self.config.stream_path)
                if text:
                    if first_token_ms is None:
                        first_token_ms = (time.perf_counter() - started) * 1000
                    pieces.append(text)
                if self.config.usage_paths is not None:
                    input_path, output_path = self.config.usage_paths
                    counts = (
                        optional_count(event.data, input_path),
                        optional_count(event.data, output_path),
                    )
                    if counts[0] is not None and counts[1] is not None:
                        input_tokens, output_tokens = counts
        elapsed_ms = (time.perf_counter() - started) * 1000
        if not events:
            raise TransportFailure(f"stream from {self.url} carried no events", status_code)
        if not pieces:
            raise TargetConfigError(
                f"stream_path {self.config.stream_path!r} selected no text in {events} "
                f"event(s); last event: {json.dumps(last_event)[:200]}"
            )
        return TargetResponse(
            "".join(pieces),
            status_code,
            elapsed_ms,
            last_event,
            total_bytes,
            input_tokens,
            output_tokens,
            first_token_ms,
        )


@dataclass(frozen=True)
class StreamEvent:
    data: Any
    size: int


def decode_event(text: str, url: str) -> Any:
    try:
        return json.loads(text)
    except ValueError as exc:
        raise TransportFailure(f"stream event from {url} is not JSON: {text[:200]!r}") from exc


# SSE: an event is one or more "data:" lines ended by a blank line, "[DONE]" ends the
# stream, and other fields and comments are skipped. NDJSON: one JSON object per line.
async def iter_events(
    response: httpx.Response, stream_format: str, url: str
) -> AsyncIterator[StreamEvent]:
    data_lines: list[str] = []
    size = 0
    # aiter_lines strips the line endings, so one byte per line stands in for them. The
    # generator is closed explicitly: a stream that ends at [DONE] would otherwise leave
    # it to a finaliser that cannot await.
    lines = response.aiter_lines()
    try:
        async for raw in lines:
            line = raw.rstrip("\r\n")
            if stream_format == "ndjson":
                if line.strip():
                    yield StreamEvent(decode_event(line, url), len(raw.encode("utf-8")) + 1)
                continue
            size += len(raw.encode("utf-8")) + 1
            if line == "":
                if data_lines:
                    payload = "\n".join(data_lines)
                    data_lines, event_size, size = [], size, 0
                    if payload.strip() == "[DONE]":
                        return
                    yield StreamEvent(decode_event(payload, url), event_size)
                continue
            if line.startswith(":"):
                continue
            field, _, value = line.partition(":")
            if field == "data":
                data_lines.append(value.removeprefix(" "))
    finally:
        # httpx types the iterator without aclose; at runtime it is an async generator.
        closer = getattr(lines, "aclose", None)
        if closer is not None:
            await closer()
    if data_lines:
        payload = "\n".join(data_lines)
        if payload.strip() != "[DONE]":
            yield StreamEvent(decode_event(payload, url), size)


# A chunk without the path (a role preamble, a finish marker) is not an error: only a
# stream that never yields text is.
def delta_text(data: Any, path: str) -> str | None:
    try:
        value = extract_path(data, path)
    except TargetConfigError:
        return None
    return value if isinstance(value, str) else None


def optional_count(data: Any, path: str) -> int | None:
    try:
        value = extract_path(data, path)
    except TargetConfigError:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return int(value)
