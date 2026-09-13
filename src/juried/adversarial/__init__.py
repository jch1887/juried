"""The built-in adversarial pack and the criteria a project sees when it is enabled."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any

import yaml

from juried.config import Config
from juried.criteria import Criterion, load_criteria
from juried.scenarios import Scenario, parse_scenario_file

PACK_FILE = "adversarial-pack.yaml"


@dataclass(frozen=True)
class Pack:
    criteria: list[Criterion]
    scenarios: list[Scenario]
    # The scenario entries as written, for writing the file a project runs.
    entries: list[dict[str, Any]]


def load_pack() -> Pack:
    text = resources.files("juried.adversarial").joinpath("pack.yaml").read_text("utf-8")
    raw = yaml.safe_load(text)
    criteria = [
        Criterion(str(c["id"]), str(c["title"]), " ".join(str(c["description"]).split()))
        for c in raw["criteria"]
    ]
    entries = list(raw["scenarios"])
    scenarios = parse_scenario_file(yaml.safe_dump({"scenarios": entries}, sort_keys=False))
    return Pack(criteria, scenarios, entries)


def pack_output_path(config: Config) -> Path:
    return config.scenarios_path / "generated" / PACK_FILE


def pack_file_text() -> str:
    header = (
        "# juried's built-in adversarial pack, written by `juried generate --adversarial` with\n"
        "# adversarial_pack = true under [generate]. Edit freely; regenerate with --force.\n"
    )
    return header + yaml.safe_dump(
        {"scenarios": load_pack().entries}, sort_keys=False, allow_unicode=True, width=88
    )


# The criteria a project's scenarios may refer to: its own file, plus the pack's when the
# pack is enabled, so the pack's scenarios collect and report under their own headings.
def criteria_for(config: Config) -> list[Criterion]:
    criteria = load_criteria(config.criteria_path)
    if config.generate.adversarial_pack:
        own = {criterion.id for criterion in criteria}
        criteria.extend(c for c in load_pack().criteria if c.id not in own)
    return criteria
