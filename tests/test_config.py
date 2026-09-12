from pathlib import Path

import pytest

from juried.config import ConfigError, find_config, load_config, parse_config

MINIMAL = """
[target]
url = "http://127.0.0.1:9/chat"
"""


def test_defaults(tmp_path: Path) -> None:
    config = parse_config(MINIMAL, tmp_path, environ={})
    assert config.run.runs == 20
    assert config.run.threshold == 0.7
    assert config.run.concurrency == 4
    assert config.run.cache_responses is False
    assert config.judge.concurrency == 4
    assert config.judge.provider == "anthropic"
    assert config.judge.temperature is None
    assert config.judge.votes == 1
    assert config.calibration_path == tmp_path / "calibration"
    assert config.target.body == {"message": "{{message}}", "history": "{{history}}"}
    assert config.target.response_path == "reply"
    assert config.criteria_path == tmp_path / "acceptance.md"
    assert config.scenarios_path == tmp_path / "scenarios"
    assert config.cache_path == tmp_path / ".juried"
    assert config.report_path == tmp_path / "reports"


def test_full_config(tmp_path: Path) -> None:
    text = """
[target]
url = "http://localhost:8000/v1/chat"
headers = { Authorization = "Bearer ${TOKEN}" }
body = { input = "{{message}}", context = "{{history}}", stream = false }
response_path = "choices.0.message.content"
timeout_seconds = 5

[criteria]
file = "docs/criteria.md"
scenarios_dir = "tests/scenarios"

[run]
runs = 20
threshold = 0.85
concurrency = 2

[judge]
provider = "openai"
model = "gpt-4.1"

[generate]
model = "gpt-4.1-mini"
scenarios_per_criterion = 6
"""
    config = parse_config(text, tmp_path, environ={})
    assert config.target.body["stream"] is False
    assert config.criteria_path == tmp_path / "docs" / "criteria.md"
    assert config.run.runs == 20
    assert config.judge.provider == "openai"
    assert config.generate_provider == "openai"
    stub = parse_config(MINIMAL + '[judge]\nprovider = "stub"\nmodel = "x"\n', tmp_path, environ={})
    assert stub.judge.model == "stub"
    assert config.generate_model == "gpt-4.1-mini"


def test_env_overrides(tmp_path: Path) -> None:
    environ = {
        "JURIED_RUN_RUNS": "3",
        "JURIED_GENERATE_TEMPERATURE": "0.9",
        "JURIED_CRITERIA_FILE": "criteria.md",
        "JURIED_RUN_CACHE_DIR": "/tmp/elsewhere",
        "JURIED_JUDGE_MODEL": "claude-opus-5",
        "JURIED_TARGET_URL": "http://override/chat",
        "JURIED_UNKNOWN_KEY": "ignored",
        "OTHER": "ignored",
    }
    config = parse_config(MINIMAL, tmp_path, environ=environ)
    assert config.run.runs == 3
    assert config.cache_path == Path("/tmp/elsewhere")
    assert config.judge.model == "claude-opus-5"
    assert config.target.url == "http://override/chat"
    assert config.generate.temperature == 0.9
    assert config.criteria.file == Path("criteria.md")


def test_generation_settings_do_not_borrow_the_judge_temperature(tmp_path: Path) -> None:
    text = MINIMAL + '[judge]\nprovider = "openai"\nmodel = "gpt-4.1"\ntemperature = 0.0\n'
    text += 'base_url = "http://judge.test"\n'
    config = parse_config(text, tmp_path, environ={})
    assert config.judge.temperature == 0.0
    assert config.generate_temperature is None
    assert config.generate_base_url == "http://judge.test"
    same = parse_config(text + "[generate]\ntemperature = 1.0\n", tmp_path, environ={})
    assert same.generate_temperature == 1.0
    other = parse_config(text + '[generate]\nprovider = "anthropic"\n', tmp_path, environ={})
    assert other.generate_base_url is None
    explicit = parse_config(
        text + '[generate]\nprovider = "anthropic"\nbase_url = "http://gen.test"\n',
        tmp_path,
        environ={},
    )
    assert explicit.generate_base_url == "http://gen.test"


def test_prices_are_optional_but_paired(tmp_path: Path) -> None:
    assert parse_config(MINIMAL, tmp_path, environ={}).judge.prices is None
    paired = parse_config(MINIMAL + "[judge]\ninput_price = 2\noutput_price = 10\n", tmp_path)
    assert paired.judge.prices == (2.0, 10.0)
    assert paired.generate_prices == (2.0, 10.0)
    other = parse_config(
        MINIMAL + '[judge]\ninput_price = 2\noutput_price = 10\n[generate]\nmodel = "x"\n',
        tmp_path,
    )
    assert other.generate_prices is None
    with pytest.raises(ConfigError, match="set together"):
        parse_config(MINIMAL + "[judge]\ninput_price = 2\n", tmp_path, environ={})


def test_votes_must_be_odd(tmp_path: Path) -> None:
    assert parse_config(MINIMAL + "[judge]\nvotes = 3\n", tmp_path, environ={}).judge.votes == 3
    with pytest.raises(ConfigError, match="odd"):
        parse_config(MINIMAL + "[judge]\nvotes = 2\n", tmp_path, environ={})


def test_unknown_keys_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid"):
        parse_config(MINIMAL + "\n[run]\nrun = 3\n", tmp_path, environ={})


def test_missing_target_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="target"):
        parse_config("[run]\nruns = 2\n", tmp_path, environ={})


def test_invalid_toml_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="TOML"):
        parse_config("[target\nurl = 1", tmp_path, environ={})


def test_load_and_find(tmp_path: Path) -> None:
    (tmp_path / "juried.toml").write_text(MINIMAL)
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    found = find_config(nested)
    assert found == tmp_path / "juried.toml"
    config = load_config(found, environ={})
    assert config.root == tmp_path.resolve()
    assert find_config(Path("/")) is None
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "missing.toml", environ={})
