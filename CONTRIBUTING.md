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
API still accepts it. `tests/test_live.py` sends, per provider, two judge requests that must
come back as parseable verdicts (one pass, one fail) and one generate request that must
come back as valid `ScenarioDraft`s, and checks that every response carried non zero token
counts. It is deselected by default and runs locally with

```
JURIED_LIVE=1 ANTHROPIC_API_KEY=... OPENAI_API_KEY=... pytest -m live -v
```

A missing key skips that provider, so with one key set you exercise one provider. The
default models are `claude-haiku-4-5` and `gpt-4.1-mini`; override them with
`JURIED_LIVE_ANTHROPIC_MODEL` and `JURIED_LIVE_OPENAI_MODEL`. A run costs well under a
penny. One more test sends a judge request through the OpenAI client to a local Ollama,
standing in for every OpenAI compatible endpoint; it looks at
`JURIED_LIVE_OLLAMA_BASE_URL` (default `http://127.0.0.1:11434/v1`) for a model named by
`JURIED_LIVE_OLLAMA_MODEL` (default `llama3.2`) and skips, with the reason in the log, when
neither is there.

The `Live provider contract` workflow runs the same command weekly and on demand from the
Actions tab. It needs the repository secrets `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`
(a missing one skips that provider, so check the log rather than the tick) and reads the
optional repository variables `JURIED_LIVE_ANTHROPIC_MODEL`, `JURIED_LIVE_OPENAI_MODEL`,
`JURIED_LIVE_OLLAMA_BASE_URL` and `JURIED_LIVE_OLLAMA_MODEL`. GitHub's hosted runners have
no Ollama, so the workflow prints a notice that the compatible endpoint test was skipped;
a self hosted runner with Ollama and the model pulled exercises it. It uploads the pytest output as the `live-pytest-output`
artifact on every run, pass or fail, so a green run can be inspected for which providers
actually ran. Run it before a release and after any change to a provider module.

## Layout

- `src/juried/config.py`, `criteria.py`, `scenarios.py`: models and file parsing.
- `src/juried/targets/`: the system under test, currently an HTTP endpoint target.
- `src/juried/judge/`: providers. `base.py` holds the interface and the shared judge and
  generation logic, `prompts.py` the pinned prompts, `stub.py` a key free provider.
- `src/juried/runner.py`: repeated runs, concurrency and caching.
- `src/juried/calibrate.py`: `juried calibrate`, the judge against human labelled responses.
- `src/juried/compare.py`: `juried compare`, regressions between two JSON reports.
- `src/juried/pytest_plugin.py`: collection of YAML scenarios as pytest items.
- `src/juried/report/`: JSON and single file HTML output.
- `src/juried/cli.py`: `init`, `generate`, `calibrate`, `compare` and `run`.

The README's roadmap lists what is not built yet; each item maps to one of the extension
points above (`Target`, `Provider`, the scenario models, the generation prompt).

## House rules

- UK English in code, comments, docs and output
- Comment only what the code cannot say. No docstrings that restate a name.
- Keep dependencies to the current list unless there is a strong reason.
- Type hints on public functions; ruff and mypy stay clean without disabling rules.
- Bump `PROMPT_VERSION` in `judge/prompts.py` whenever a prompt changes, so cached
  verdicts from the old prompt are not reused.
