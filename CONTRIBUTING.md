# Contributing

## Setup

```
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
make check
```

`make check` runs ruff, mypy (strict) and the test suite. All three must be clean before
a change is ready. `make example` starts the fake FAQ bot, generates scenarios and runs
them end to end without API keys.

## Layout

- `src/juried/config.py`, `criteria.py`, `scenarios.py`: models and file parsing.
- `src/juried/targets/`: the system under test. Stage 1 has an HTTP endpoint target.
- `src/juried/judge/`: providers. `base.py` holds the interface and the shared judge and
  generation logic, `prompts.py` the pinned prompts, `stub.py` a key free provider.
- `src/juried/runner.py`: repeated runs, concurrency and caching.
- `src/juried/calibrate.py`: `juried calibrate`, the judge against human labelled responses.
- `src/juried/pytest_plugin.py`: collection of YAML scenarios as pytest items.
- `src/juried/report/`: JSON and single file HTML output.
- `src/juried/cli.py`: `init`, `generate`, `calibrate` and `run`.

Extension points for later stages: a new `Target` implementation for UI driving, a new
`Provider` for other judges, a `turns` field on `Scenario` for multi turn conversations,
and new scenario kinds for adversarial generation.

## House rules

- UK English in code, comments, docs and output
- Comment only what the code cannot say. No docstrings that restate a name.
- Keep dependencies to the current list unless there is a strong reason.
- Type hints on public functions; ruff and mypy stay clean without disabling rules.
- Bump `PROMPT_VERSION` in `judge/prompts.py` whenever a prompt changes, so cached
  verdicts from the old prompt are not reused.
