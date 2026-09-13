from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from juried.scenarios import Turn


@dataclass(frozen=True)
class TargetResponse:
    text: str
    status_code: int
    elapsed_ms: float
    raw: Any = None
    bytes: int = 0
    # Token counts from the reply, when [target] names the paths to them.
    input_tokens: int | None = None
    output_tokens: int | None = None
    # Time to the first text delta, for a streaming target; None otherwise.
    first_token_ms: float | None = None


class Target(Protocol):
    def fingerprint(self) -> str: ...

    async def send(self, message: str, history: Sequence[Turn]) -> TargetResponse: ...
