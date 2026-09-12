<p align="center">
  <img src="https://raw.githubusercontent.com/jch1887/juried/main/juried.png" alt="juried" width="360">
  <br>
  Acceptance testing for LLM features, built for QA teams.
</p>

juried treats your LLM powered feature as a black box behind an HTTP endpoint. You write
acceptance criteria in plain English, juried generates test scenarios from them, runs each
scenario repeatedly, has a pinned LLM judge mark every response, and reports pass rates
with confidence intervals. Scenarios are ordinary pytest tests, so `-k`, `-x`, markers
and `--junitxml` all work and the results fit an existing CI job. The repeated runs are
of the feature, not of the verdict: by default each response gets one verdict from one
model, and `juried calibrate` tells you how far to trust that model.

## Install

```
pip install juried
```

Python 3.12 or later. Judges read `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from the
environment; nothing else is ever read from disk.

## Three commands

```
juried init        # writes juried.toml and an example acceptance.md
juried generate    # turns each criterion into scenarios/generated/<criterion>.yaml
juried run         # runs every scenario N times under pytest and writes the report
```

A fourth, `juried calibrate`, judges responses your team has already labelled and reports
how often the judge agrees. See "Trusting the judge" below.

`juried run` accepts pytest arguments after its own, for example
`juried run --runs 20 -k refunds -x --junitxml=out.xml`. Generation is a one off step:
review and edit the generated YAML, commit it, and `run` never touches it.

## Configuration

`juried.toml` lives at the repo root. Any key can be overridden with an environment
variable named `JURIED_<SECTION>_<KEY>`, such as `JURIED_RUN_RUNS=20`. `.env.example`
lists every variable juried reads, including the provider keys and the overrides;
copy it to `.env` and export it from your shell, since juried does not load it itself.

```toml
[target]
url = "https://staging.example.com/api/chat"
headers = { Authorization = "Bearer ${STAGING_TOKEN}" }
body = { message = "{{message}}", history = "{{history}}" }
response_path = "choices.0.message.content"

[run]
runs = 10          # attempts per scenario
threshold = 0.7    # floor on the lower bound of the Wilson 95% interval, not a pass rate
concurrency = 4

[judge]
provider = "anthropic"    # anthropic, openai or stub
model = "claude-sonnet-5" # pinned and recorded with every verdict
```

Set `temperature` under `[judge]` only for a model that accepts it. `claude-sonnet-5` rejects
the parameter, so the example leaves it out; when it is unset nothing is sent and the report
says so.

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

The judge sees each part of the scenario in its own delimited section and is told that the
response is untrusted output which may contain instructions or claims about the verdict, so a
response that says "this meets the expectation, pass" is judged on what it does for the user,
not on what it says about the test. The prompt is pinned and its version is part of every
verdict's cache key, so a prompt change never reuses an old verdict.

A phrase in double quotes inside `expected` must appear in the response word for word,
ignoring case. Text outside quotes is judged on meaning. With
`expected: Says returns are accepted within "14 days" for a full refund`, a response saying
"you have 14 days and get every penny back" passes, while "a fortnight for a full refund"
fails because `14 days` is missing. The stub judge applies the same rule.

## How a scenario passes

Each scenario runs `runs` times. The judge marks each response pass or fail with a one
line reason, using a fixed prompt and the configured temperature, if any. juried computes the
pass rate and its Wilson 95% interval, and the scenario passes when the lower bound meets
`threshold`.

**The threshold is not a pass rate.** It is a floor on the lower bound of the confidence
interval, and with a small number of runs that bound sits well below the observed rate.
**With the defaults, `runs = 10` and `threshold = 0.7`, a scenario must pass 10 times out
of 10.** One miss gives 9/10, whose lower bound is 0.60, and the gate fails. A 90% pass
rate does not pass the default gate. Tolerating misses means running more times:

| runs | threshold | passes needed | misses tolerated |
|-----:|----------:|--------------:|-----------------:|
|   10 |       0.7 |            10 |                0 |
|   20 |       0.7 |            19 |                1 |
|   30 |       0.7 |            26 |                4 |
|   50 |       0.7 |            42 |                8 |
|  100 |       0.7 |            79 |               21 |
|   10 |       0.5 |             9 |                1 |
|   20 |       0.8 |            20 |                0 |
|   50 |       0.8 |            46 |                4 |
|   10 |       0.9 |  never passes |                  |

juried prints what the gate needs at the top of every run
(`juried: gate needs 10/10 passes at threshold 0.70 (no misses tolerated)`), repeats it in
every gate failure, shows it under the threshold in the report, and warns when a gate can
never pass, as with 10 runs at 0.9 where even a perfect score has a lower bound of 0.72.
Decide on the number of misses you are willing to accept, then pick `runs` from the table;
raising `threshold` alone only makes the gate stricter.

A failing gate is a normal pytest failure that shows the pass rate, the interval, the
threshold and the first failing transcript with the judge's reason. An HTTP error from
your endpoint is reported as a transport error, separately from a judge fail. Every verdict
is appended to `.juried/verdicts.jsonl` with the judge model and timestamp.

## Caching

Every run samples the feature afresh. That is the point of the tool: a scenario only shows
its flakiness if each attempt is a new request, so responses are never replayed by default.
Verdicts are cached in `.juried/cache/verdicts` by content hash, so a response the judge
has already seen, word for word, is not judged again; the cache key includes the judge
model, temperature and prompt version, and `--no-cache` bypasses it.

For development you can opt in to replaying responses with `juried run --cache-responses`
(or `cache_responses = true` under `[run]`). Responses are then stored by target, message,
history and attempt number and read back on the next run, which makes the run free but
also frozen: a replayed scenario returns the same attempts every time and cannot detect
non-determinism. juried refuses to let that pass quietly. The header says
`cache verdicts and responses`, each replayed scenario is marked in the pytest output and
in the report, and the summary ends with a warning counting the replayed responses.
Do not cache `.juried/cache/responses` in CI, and do not set `cache_responses` there.

## Trusting the judge

An LLM judge is a model like any other, and juried does not pretend otherwise. What it
gives you:

- **One verdict per response by default.** Each response is judged once by the configured
  model. The judge's reason is stored with every verdict, and every verdict is appended to
  `.juried/verdicts.jsonl` with the model, prompt version and timestamp, so any verdict can
  be audited later.
- **Votes, when you want agreement measured.** Set `votes = 3` (any odd number) under
  `[judge]` to judge each response that many times and take the majority. The report then
  shows the number of split verdicts per scenario and the pytest summary flags them
  (`judge split on 2`). Split verdicts mean the expectation is ambiguous or the judge is
  unreliable on it; either way, look at the wording before trusting the number.
- **Calibration against human labels.** Put responses your team has judged by hand under
  `calibration/`, then run `juried calibrate`. It judges each one with the configured
  model and prints every disagreement, the accuracy, and the counts of false passes and
  false fails. `--min-accuracy 0.9` makes it exit non zero below that figure, so a judge
  change is caught in CI. The result is also written to `reports/juried-calibration.json`.

```yaml
criterion: refund-policy
cases:
  - name: Vague answer without the window
    message: I want my money back on a jumper that does not fit.
    expected: States the "14 days" return window and that the refund is "full".
    response: Returns are accepted for a full refund, please contact us to arrange one.
    verdict: fail
    note: drops the 14 day window
```

Label at least a handful of cases per criterion, including borderline responses and ones
that contain the right words for the wrong reason. Rerun `calibrate` whenever the judge
model, temperature or prompt changes.

## The report

After a run juried writes `reports/juried-report.html` and `reports/juried-report.json`.
The HTML is a single self contained file with no scripts. It opens with the totals, then
lists each acceptance criterion with its description, a table of its scenarios showing
passes, pass rate, interval, threshold, response latency and gate result, and beneath the
table each scenario's message, expectation and every failing run with the response and
the judge's reason. Criteria with no scenarios are called out so coverage gaps are
visible. The JSON file holds the same structure plus every attempt, for anyone who wants
to chart trends.

<img src="https://raw.githubusercontent.com/jch1887/juried/main/docs/report.png" alt="juried report showing three scenarios, one at 7 of 10 failing because its lower bound is below the threshold" width="900">

In the third row seven of ten runs passed and the observed rate meets the threshold, yet the
gate still fails because the lower bound does not.

## Try it without API keys

```
cd examples/faq-bot
python server.py &      # deterministic fake FAQ bot on port 8765
juried generate          # uses the stub provider from juried.toml
juried run
```

The stub judge is a substring matcher: it passes any non empty response that contains every
`"quoted phrase"` in `expected` and ignores the rest of the expectation. It shows the
mechanics of runs, gates and reports, and says nothing about how an LLM judge behaves.
One hand written refund scenario fails its gate on purpose, because the fake bot drops
the 14 day detail every fourth time, which is the kind of flakiness juried exists to catch.

`juried calibrate` in the same directory runs the stub against the labelled responses in
`calibration/refunds.yaml` and reports where it disagrees with the human labels. The stub
gets the "right words, wrong answer" cases wrong, which is the point: calibrate before you
trust any judge, and swap `provider` for a real one when you have a key.

## Development

```
make check      # ruff, mypy and pytest
```

See CONTRIBUTING.md for the development install.

Licensed under the MIT licence.
