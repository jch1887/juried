from __future__ import annotations

from collections.abc import Sequence

from juried.checks import contains, quoted_phrases
from juried.criteria import Criterion
from juried.judge.base import Provider, Verdict
from juried.scenarios import Scenario, ScenarioDraft, Turn


class StubProvider(Provider):
    """Deterministic provider for examples and tests. Passes when the response is non empty and
    contains every double quoted phrase from the scenario's expectation, matched with the
    same normalisation as a `contains` check unless strict_quotes is set."""

    name = "stub"

    def __init__(self, model: str = "stub", strict_quotes: bool = False) -> None:
        self.model = model
        self.strict_quotes = strict_quotes

    def fingerprint(self) -> str:
        return f"stub:{self.model}"

    async def judge(
        self,
        criterion: Criterion,
        scenario: Scenario,
        response_text: str,
        transcript: Sequence[Turn] = (),
    ) -> Verdict:
        if not response_text.strip():
            return Verdict(False, "response was empty", self.model, Verdict.now())
        for phrase in quoted_phrases(scenario.expected):
            if not contains(response_text, phrase, self.strict_quotes):
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
