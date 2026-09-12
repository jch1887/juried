# Contributing

## Setup

```
uv venv --python 3.12 .venv && source .venv/bin/activate
uv pip install -e ".[dev]"
make check
```

`make check` runs ruff, mypy (strict) and the test suite. All three must be clean before
a change is ready, and the `Check` workflow runs the same target on every pull request and
push to `main`, on Python 3.11, 3.12 and 3.13, then builds the wheel that a release would
publish. `make example` starts the fake FAQ bot, generates scenarios and runs them end to
end without API keys.

## Live provider tests

The unit tests only assert what juried sends to Anthropic and OpenAI, never that either
API still accepts it. `tests/test_live.py` sends one judge and one generate request per
provider to the real API. It is deselected by default and runs with

```
JURIED_LIVE=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... pytest -m live
```

A missing key skips that provider. The `Live provider contract` workflow runs it weekly
and on demand from the Actions tab, using the repository secrets `ANTHROPIC_API_KEY` and
`OPENAI_API_KEY`. Run it before a release and after any change to a provider module.

## Layout

- `src/juried/config.py`, `criteria.py`, `scenarios.py`: models and file parsing.
- `src/juried/targets/`: the system under test, currently an HTTP endpoint target.
- `src/juried/judge/`: providers. `base.py` holds the interface and the shared judge and
  generation logic, `prompts.py` the pinned prompts, `stub.py` a key free provider.
- `src/juried/runner.py`: repeated runs, concurrency and caching.
- `src/juried/calibrate.py`: `juried calibrate`, the judge against human labelled responses.
- `src/juried/pytest_plugin.py`: collection of YAML scenarios as pytest items.
- `src/juried/report/`: JSON and single file HTML output.
- `src/juried/cli.py`: `init`, `generate`, `calibrate` and `run`.

The README's roadmap lists what is not built yet; each item maps to one of the extension
points above (`Target`, `Provider`, the scenario models, the generation prompt).

## House rules

- UK English in code, comments, docs and output
- Comment only what the code cannot say. No docstrings that restate a name.
- Keep dependencies to the current list unless there is a strong reason.
- Type hints on public functions; ruff and mypy stay clean without disabling rules.
- Bump `PROMPT_VERSION` in `judge/prompts.py` whenever a prompt changes, so cached
  verdicts from the old prompt are not reused.
