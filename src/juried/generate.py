from __future__ import annotations

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from juried.config import Config
from juried.criteria import Criterion, slugify
from juried.judge.base import Provider
from juried.scenarios import (
    Scenario,
    ScenarioDraft,
    dump_scenario_file,
    load_scenario_file,
    scenario_files,
)

GENERATED_DIR = "generated"


@dataclass
class GenerationOutcome:
    written: list[Path] = field(default_factory=list)
    skipped: list[Path] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)


def output_path(config: Config, criterion: Criterion) -> Path:
    return config.scenarios_path / GENERATED_DIR / f"{criterion.id}.yaml"


def existing_ids(config: Config, exclude: set[Path]) -> set[str]:
    ids: set[str] = set()
    for path in scenario_files(config.scenarios_path):
        if path in exclude:
            continue
        ids.update(scenario.id for scenario in load_scenario_file(path))
    return ids


def drafts_to_scenarios(
    criterion: Criterion, drafts: Sequence[ScenarioDraft], taken: set[str]
) -> list[Scenario]:
    scenarios: list[Scenario] = []
    for draft in drafts:
        base = f"{criterion.id}-{slugify(draft.name) or 'scenario'}"
        candidate = base
        suffix = 2
        while candidate in taken:
            candidate = f"{base}-{suffix}"
            suffix += 1
        taken.add(candidate)
        scenarios.append(
            Scenario(
                id=candidate,
                criterion=criterion.id,
                name=draft.name,
                kind=draft.kind,
                message=draft.message,
                expected=draft.expected,
            )
        )
    return scenarios


async def _generate_all(
    provider: Provider, targets: list[Criterion], count: int, concurrency: int
) -> list[list[ScenarioDraft]]:
    semaphore = asyncio.Semaphore(concurrency)

    async def one(criterion: Criterion) -> list[ScenarioDraft]:
        async with semaphore:
            return await provider.generate(criterion, count)

    async with provider:
        return list(await asyncio.gather(*(one(criterion) for criterion in targets)))


def generate_scenarios(
    config: Config,
    criteria: Sequence[Criterion],
    provider: Provider,
    *,
    force: bool = False,
    only: set[str] | None = None,
) -> GenerationOutcome:
    outcome = GenerationOutcome()
    targets: list[Criterion] = []
    for criterion in criteria:
        if only is not None and criterion.id not in only:
            continue
        path = output_path(config, criterion)
        if path.exists() and not force:
            outcome.skipped.append(path)
            continue
        targets.append(criterion)
    if not targets:
        return outcome

    drafts = asyncio.run(
        _generate_all(
            provider, targets, config.generate.scenarios_per_criterion, config.run.concurrency
        )
    )
    replaced = {output_path(config, criterion) for criterion in targets}
    taken = existing_ids(config, exclude=replaced)
    for criterion, criterion_drafts in zip(targets, drafts, strict=True):
        scenarios = drafts_to_scenarios(criterion, criterion_drafts, taken)
        path = output_path(config, criterion)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(dump_scenario_file(criterion.id, scenarios), encoding="utf-8")
        outcome.written.append(path)
        outcome.counts[criterion.id] = len(scenarios)
    return outcome
