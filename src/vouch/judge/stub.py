from __future__ import annotations

import re

from vouch.criteria import Criterion
from vouch.judge.base import Provider, Verdict
from vouch.scenarios import Scenario, ScenarioDraft

QUOTED = re.compile(r'"([^"]+)"')


class StubProvider(Provider):
    """Deterministic provider for examples and tests. Passes when the response is non empty and
    contains every double quoted phrase from the scenario's expectation."""

    name = "stub"

    def __init__(self, model: str = "stub") -> None:
        self.model = model

    def fingerprint(self) -> str:
        return f"stub:{self.model}"

    async def judge(self, criterion: Criterion, scenario: Scenario, response_text: str) -> Verdict:
        if not response_text.strip():
            return Verdict(False, "response was empty", self.model, Verdict.now())
        lowered = response_text.lower()
        for phrase in QUOTED.findall(scenario.expected):
            if phrase.lower() not in lowered:
                return Verdict(
                    False, f"response does not mention {phrase!r}", self.model, Verdict.now()
                )
        return Verdict(True, "response mentions every expected phrase", self.model, Verdict.now())

    async def generate(self, criterion: Criterion, count: int) -> list[ScenarioDraft]:
        topic = criterion.title.lower()
        expected = " ".join(criterion.description.split()) or f"Addresses {topic}."
        drafts = [
            ScenarioDraft(
                name="Direct question",
                kind="happy_path",
                message=f"Can you tell me about {topic}?",
                expected=expected,
            ),
            ScenarioDraft(
                name="Terse request",
                kind="edge_case",
                message=f"{topic}??",
                expected=expected,
            ),
        ]
        return drafts[:count]
