from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx

RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})
BACKOFF_SECONDS = 0.5


class TransportFailure(Exception):
    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


async def request_json(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Any,
    retries: int,
) -> httpx.Response:
    attempt = 0
    while True:
        try:
            response = await client.request(method, url, headers=headers, json=body)
        except httpx.TransportError as exc:
            if attempt >= retries:
                raise TransportFailure(f"{type(exc).__name__} calling {url}: {exc}") from exc
        else:
            if response.status_code < 400:
                return response
            if response.status_code not in RETRYABLE_STATUSES or attempt >= retries:
                raise TransportFailure(
                    f"HTTP {response.status_code} from {url}: {excerpt(response.text)}",
                    status_code=response.status_code,
                )
        attempt += 1
        await asyncio.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))


# The streaming twin of request_json: the same retries before any byte of the body is
# read, then the open response for the caller to iterate.
@asynccontextmanager
async def stream_response(
    client: httpx.AsyncClient,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    body: Any,
    retries: int,
) -> AsyncIterator[httpx.Response]:
    attempt = 0
    while True:
        try:
            request = client.build_request(method, url, headers=headers, json=body)
            response = await client.send(request, stream=True)
        except httpx.TransportError as exc:
            if attempt >= retries:
                raise TransportFailure(f"{type(exc).__name__} calling {url}: {exc}") from exc
        else:
            if response.status_code < 400:
                try:
                    yield response
                finally:
                    await response.aclose()
                return
            text = (await response.aread()).decode("utf-8", errors="replace")
            await response.aclose()
            if response.status_code not in RETRYABLE_STATUSES or attempt >= retries:
                raise TransportFailure(
                    f"HTTP {response.status_code} from {url}: {excerpt(text)}",
                    status_code=response.status_code,
                )
        attempt += 1
        await asyncio.sleep(BACKOFF_SECONDS * 2 ** (attempt - 1))


def excerpt(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."
