from pathlib import Path

from juried.config import parse_config
from juried.criteria import Criterion
from juried.generate import generate_scenarios, output_path
from juried.judge import StubProvider
from juried.scenarios import load_scenarios

CRITERIA = [
    Criterion("hours", "Opening hours", "States the hours."),
    Criterion("refunds", "Refund policy", "Refunds within 14 days."),
]


def test_generate_writes_reviewable_yaml(tmp_path: Path) -> None:
    config = parse_config('[target]\nurl = "http://x/"\n[judge]\nprovider = "stub"\n', tmp_path)
    outcome = generate_scenarios(config, CRITERIA, StubProvider())
    assert [p.name for p in outcome.written] == ["hours.yaml", "refunds.yaml"]
    assert outcome.counts == {"hours": 2, "refunds": 2}
    assert outcome.written[0] == tmp_path / "scenarios" / "generated" / "hours.yaml"
    text = outcome.written[0].read_text()
    assert text.startswith("criterion: hours\n")
    assert "kind: happy_path" in text
    scenarios = load_scenarios(config.scenarios_path)
    assert [s.id for s in scenarios] == [
        "hours-direct-question",
        "hours-terse-request",
        "refunds-direct-question",
        "refunds-terse-request",
    ]


def test_generate_skips_existing_unless_forced(tmp_path: Path) -> None:
    config = parse_config('[target]\nurl = "http://x/"\n[judge]\nprovider = "stub"\n', tmp_path)
    generate_scenarios(config, CRITERIA, StubProvider())
    path = output_path(config, CRITERIA[0])
    path.write_text("criterion: hours\nscenarios: []\n")
    outcome = generate_scenarios(config, CRITERIA, StubProvider())
    assert outcome.written == []
    assert outcome.skipped == [path, output_path(config, CRITERIA[1])]
    assert path.read_text() == "criterion: hours\nscenarios: []\n"
    outcome = generate_scenarios(config, CRITERIA, StubProvider(), force=True, only={"hours"})
    assert outcome.written == [path]
    assert "Direct question" in path.read_text()


def test_generated_ids_avoid_handwritten_ones(tmp_path: Path) -> None:
    config = parse_config(
        '[target]\nurl = "http://x/"\n[judge]\nprovider = "stub"\n[generate]\n'
        "scenarios_per_criterion = 1\n",
        tmp_path,
    )
    config.scenarios_path.mkdir()
    (config.scenarios_path / "mine.yaml").write_text(
        "criterion: hours\nscenarios:\n  - name: Direct question\n    message: m\n    expected: e\n"
    )
    generate_scenarios(config, CRITERIA[:1], StubProvider())
    ids = [s.id for s in load_scenarios(config.scenarios_path)]
    assert ids == ["hours-direct-question-2", "hours-direct-question"]
