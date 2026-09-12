from pathlib import Path

import pytest

from juried.scenarios import (
    Scenario,
    ScenarioError,
    dump_scenario_file,
    load_scenarios,
    parse_scenario_file,
)

FILE = """
criterion: opening-hours
scenarios:
  - id: hours-happy
    name: Customer asks for opening hours
    kind: happy_path
    message: What time are you open?
    expected: States the hours, including "9am" and "5pm".
  - name: Customer asks late at night
    kind: edge_case
    message: are u open rn
    history:
      - role: user
        content: hi
      - role: assistant
        content: Hello, how can I help?
    expected: Gives the hours rather than guessing whether it is open now.
    runs: 3
    threshold: 0.5
    tags: [informal]
  - criterion: other
    name: Attached elsewhere
    message: hello
    expected: anything
"""


def test_parse_file() -> None:
    scenarios = parse_scenario_file(FILE)
    assert [s.id for s in scenarios] == [
        "hours-happy",
        "opening-hours-customer-asks-late-at-night",
        "other-attached-elsewhere",
    ]
    assert scenarios[0].kind == "happy_path"
    assert scenarios[0].history == []
    assert scenarios[0].runs is None
    late = scenarios[1]
    assert late.history[1].role == "assistant"
    assert late.runs == 3
    assert late.threshold == 0.5
    assert late.tags == ["informal"]
    assert scenarios[2].criterion == "other"


def test_unknown_field_rejected() -> None:
    with pytest.raises(ScenarioError, match="invalid"):
        parse_scenario_file(
            "criterion: c\nscenarios:\n  - name: n\n    message: m\n    expect: x\n"
        )


def test_missing_criterion_rejected() -> None:
    with pytest.raises(ScenarioError, match="no criterion"):
        parse_scenario_file("scenarios:\n  - name: n\n    message: m\n    expected: x\n")


def test_invalid_yaml_rejected() -> None:
    with pytest.raises(ScenarioError, match="YAML"):
        parse_scenario_file("scenarios: [")


def test_empty_file_gives_nothing() -> None:
    assert parse_scenario_file("") == []


def test_load_directory_and_uniqueness(tmp_path: Path) -> None:
    (tmp_path / "a.yaml").write_text(FILE)
    nested = tmp_path / "generated"
    nested.mkdir()
    (nested / "b.yml").write_text(
        "criterion: c\nscenarios:\n  - id: hours-happy\n    name: n\n    message: m\n"
        "    expected: x\n"
    )
    with pytest.raises(ScenarioError, match="duplicate scenario id"):
        load_scenarios(tmp_path)
    (nested / "b.yml").unlink()
    (tmp_path / "notes.txt").write_text("ignored")
    scenarios = load_scenarios(tmp_path)
    assert len(scenarios) == 3
    assert scenarios[0].source == tmp_path / "a.yaml"
    assert load_scenarios(tmp_path / "missing") == []


def test_dump_round_trip() -> None:
    scenarios = [
        Scenario(
            id="c-one",
            criterion="c",
            name="One",
            kind="happy_path",
            message="hello",
            expected="says hi",
        ),
        Scenario(
            id="c-two",
            criterion="c",
            name="Two",
            message="bye",
            expected="says bye",
            runs=2,
            tags=["x"],
        ),
    ]
    text = dump_scenario_file("c", scenarios)
    assert text.startswith("criterion: c\nscenarios:\n- id: c-one\n  name: One\n")
    assert "history" not in text
    assert "threshold" not in text
    reloaded = parse_scenario_file(text)
    assert [s.model_dump() for s in reloaded] == [s.model_dump() for s in scenarios]


def test_turns_are_optional_and_non_empty() -> None:
    from pydantic import ValidationError

    plain = Scenario(id="a", criterion="c", name="n", message="m", expected="e")
    assert plain.turns == []
    talky = Scenario(
        id="a", criterion="c", name="n", message="m", expected="e", turns=["one", "two"]
    )
    assert talky.turns == ["one", "two"]
    with pytest.raises(ValidationError):
        Scenario(id="a", criterion="c", name="n", message="m", expected="e", turns=[""])
    dumped = dump_scenario_file("c", [plain, talky])
    assert dumped.count("turns:") == 1
