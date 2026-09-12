<img src="vouch.png" alt="vouch" width="360">

Acceptance testing for LLM features, built for QA teams.

vouch treats your LLM powered feature as a black box behind an HTTP endpoint. You write
acceptance criteria in plain English, vouch generates test scenarios from them, runs each
scenario repeatedly, has a pinned LLM judge mark every response, and reports pass rates
with confidence intervals. Scenarios are ordinary pytest tests, so `-k`, `-x`, markers
and `--junitxml` all work and the results fit an existing CI job.

## Install

```
pip install -e .
```

Python 3.12 or later. Judges read `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from the
environment; nothing else is ever read from disk.

## Three commands

```
vouch init        # writes vouch.toml and an example acceptance.md
vouch generate    # turns each criterion into scenarios/generated/<criterion>.yaml
vouch run         # runs every scenario N times under pytest and writes the report
```

`vouch run` accepts pytest arguments after its own, for example
`vouch run --runs 20 -k refunds -x --junitxml=out.xml`. Generation is a one off step:
review and edit the generated YAML, commit it, and `run` never touches it.

## Configuration

`vouch.toml` lives at the repo root. Any key can be overridden with an environment
variable named `VOUCH_<SECTION>_<KEY>`, such as `VOUCH_RUN_RUNS=20`.

```toml
[target]
url = "https://staging.example.com/api/chat"
headers = { Authorization = "Bearer ${STAGING_TOKEN}" }
body = { message = "{{message}}", history = "{{history}}" }
response_path = "choices.0.message.content"

[run]
runs = 10          # attempts per scenario
threshold = 0.7    # gate on the lower bound of the Wilson 95% interval
concurrency = 4

[judge]
provider = "anthropic"    # anthropic, openai or stub
model = "claude-sonnet-5" # pinned and recorded with every verdict
temperature = 0.0
```

`{{message}}` is replaced with the scenario message; a value of exactly `"{{history}}"`
becomes the earlier turns as a list of `{role, content}` objects. `response_path` is a
dotted path into the JSON reply.

## Criteria and scenarios

Criteria are `##` headings in a Markdown file. The heading becomes a stable id; add
`{#id}` to fix it explicitly.

```markdown
## Refund policy
The bot explains that any item can be returned within 14 days of delivery for a full
refund. It must state the 14 day window.

## Unknown questions {#unknown}
When the bot cannot answer it says so and gives help@example.com.
```

Generated and hand written scenarios share one YAML shape. Hand written files go
anywhere under `scenarios/`; `runs` and `threshold` may be set per scenario.

```yaml
criterion: refund-policy
scenarios:
  - name: Asks how to get money back
    kind: happy_path
    message: I want my money back on a jumper that does not fit.
    expected: States the 14 day return window and that the refund is full.
  - name: Follows up after a delivery answer
    kind: edge_case
    history:
      - role: user
        content: Can you tell me about delivery?
      - role: assistant
        content: Standard delivery takes 3 to 5 working days.
    message: and refunds?
    expected: Explains the 14 day refund window without repeating the delivery answer.
    runs: 20
    threshold: 0.8
```

## How a scenario passes

Each scenario runs `runs` times. The judge marks each response pass or fail with a one
line reason, using a fixed prompt at temperature 0. vouch computes the pass rate and its
Wilson 95% interval, and the scenario passes when the lower bound meets `threshold`. With
10 runs a perfect score gives a lower bound of 0.72, so the default threshold is 0.7; a
stricter threshold needs more runs, and vouch warns when a gate can never pass.

A failing gate is a normal pytest failure that shows the pass rate, the interval, the
threshold and the first failing transcript with the judge's reason. An HTTP error from
your endpoint is reported as a transport error, separately from a judge fail. Responses
and verdicts are cached in `.vouch/` by content hash, so re-runs during development are
cheap; pass `--no-cache` to skip it. Every verdict is appended to `.vouch/verdicts.jsonl`
with the judge model and timestamp.

## The report

After a run vouch writes `reports/vouch-report.html` and `reports/vouch-report.json`.
The HTML is a single self contained file with no scripts. It opens with the totals, then
lists each acceptance criterion with its description, a table of its scenarios showing
passes, pass rate, interval, threshold and gate result, and beneath the table each
scenario's message, expectation and every failing run with the response and the judge's
reason. Criteria with no scenarios are called out so coverage gaps are visible. The JSON
file holds the same structure plus every attempt, for anyone who wants to chart trends.

## Try it without API keys

```
cd examples/faq-bot
python server.py &      # deterministic fake FAQ bot on port 8765
vouch generate          # uses the stub provider from vouch.toml
vouch run
```

The stub judge passes a response that contains every `"quoted phrase"` in `expected`.
One hand written refund scenario fails its gate on purpose, because the fake bot drops
the 14 day detail every fourth time, which is the kind of flakiness vouch exists to catch.

## Development

```
uv pip install -e ".[dev]"
make check      # ruff, mypy and pytest
```

Licensed under the MIT licence.
