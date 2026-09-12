from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from vouch.scenarios import Turn


@dataclass(frozen=True)
class TargetResponse:
    text: str
    status_code: int
    elapsed_ms: float
    raw: Any = None


class Target(Protocol):
    def fingerprint(self) -> str: ...

    async def send(self, message: str, history: Sequence[Turn]) -> TargetResponse: ...
