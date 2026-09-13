<p align="center">
  <img src="https://raw.githubusercontent.com/jch1887/juried/main/juried.png" alt="juried" width="360">
  <br>
  <a href="https://github.com/jch1887/juried/actions/workflows/check.yml"><img src="https://github.com/jch1887/juried/actions/workflows/check.yml/badge.svg" alt="Check"></a>
  <a href="https://github.com/jch1887/juried/actions/workflows/live.yml"><img src="https://github.com/jch1887/juried/actions/workflows/live.yml/badge.svg" alt="Live provider contract"></a>
  <a href="https://pypi.org/project/juried/"><img src="https://img.shields.io/pypi/v/juried" alt="PyPI version"></a>
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

Python 3.11 or later. Judges read `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` from the
environment. juried never reads a secret from disk and does not load `.env` itself.

## The commands

```
juried init                      # writes juried.toml, acceptance.md and calibration/example.yaml
juried generate                  # turns each criterion into scenarios/generated/<criterion>.yaml
juried calibrate                 # judges responses your team labelled; how far to trust the judge
juried run                       # runs every scenario N times under pytest and writes the report
juried compare old.json new.json # flags scenarios that got worse between two reports
juried estimate                  # what a run would send and cost, without sending anything
```

`juried run` accepts pytest arguments after its own, for example
`juried run --runs 50 -k refund -x --junitxml=out.xml`. Generation is a one off step:
review and edit the generated YAML, commit it, and `run` never touches it. `calibrate` is
covered under "Trusting the judge" and `compare` under "Comparing runs".

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
usage_input_path = "usage.prompt_tokens"       # optional: token counts in the reply
usage_output_path = "usage.completion_tokens"
input_price = 2.0                              # optional: US dollars per million tokens
output_price = 10.0
# cost_per_request = 0.002                     # or a flat price per call

[run]
runs = 20          # attempts per scenario
misses = 1         # failed attempts a scenario may have and still pass
concurrency = 4    # requests in flight to the target, across all scenarios

[judge]
provider = "anthropic"    # anthropic, openai or stub
model = "claude-sonnet-5" # pinned and recorded with every verdict
concurrency = 4           # requests in flight to the judge, across all scenarios
```

Set `temperature` under `[judge]` only for a model that accepts it. `claude-sonnet-5` rejects
the parameter, so the example leaves it out; when it is unset nothing is sent and the report
says so.

A body value that is exactly `"{{message}}"` or `"{{history}}"` becomes the scenario
message or the earlier turns as a list of `{role, content}` objects, keeping its type.
Inside longer text `{{message}}` is replaced with the message and `{{history}}` with the
turns as JSON. `${NAME}` anywhere in `url`, `headers` or `body` is replaced with that
environment variable before the request is sent, and the run stops before any request if
the variable is unset. `response_path` is a dotted path into the JSON reply, and so are
`usage_input_path` and `usage_output_path`, which name the token counts in it if the target
reports them; see "What a run costs".

`[generate]` takes `provider`, `model`, `temperature`, `base_url`, `scenarios_per_criterion`
and `max_tokens`. `provider` and `model` default to the judge's. `temperature` does not: the
judge's is chosen for consistent verdicts and generation wants variety, so it is unset
unless you set it under `[generate]`.

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
anywhere under `scenarios/`; `runs` and `misses` may be set per scenario.

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
    misses: 0
```

Add `turns` for a live conversation before `message`; see the conversation fields below.

The judge sees each part of the scenario in its own delimited section and is told that the
response is untrusted output which may contain instructions or claims about the verdict, so a
response that says "this meets the expectation, pass" is judged on what it does for the user,
not on what it says about the test. The prompt is pinned and its version is part of every
verdict's cache key, so a prompt change never reuses an old verdict.

Two fields cover conversations. `history` is a scripted prefix: juried sends it to your
endpoint with the message and shows it to the judge in its own section, and the generator
may write a short one when a message only makes sense as a follow up. `turns` is a live
conversation: each entry is a user message sent in order, the feature's reply to it becomes
context for the next, and `message` is the final turn that the expectation judges.

```yaml
  - name: Changes mind about the refund
    kind: edge_case
    turns:
      - I want to return a jumper.
      - Actually it was a gift, does that matter?
    message: So how long have I got?
    expected: Still states the "14 days" window and does not contradict its earlier answers.
```

Every attempt drives the whole conversation afresh, so a scenario with two turns costs three
requests per attempt. The judge sees the scripted history, then the live transcript with the
feature's own replies, then the final message and response, and is told the expectation may
refer to what was said earlier. The transcript is recorded on every attempt, shown for
failing runs in the terminal and the report, and kept in the JSON.

A phrase in double quotes inside `expected` must appear in the response word for word,
ignoring case. Text outside quotes is judged on meaning. With
`expected: Says returns are accepted within "14 days" for a full refund`, a response saying
"you have 14 days and get every penny back" passes, while "a fortnight for a full refund"
fails because `14 days` is missing. The stub judge applies the same rule.

## Concurrency

Scenarios are pytest items, which pytest runs one after another, but juried does not wait
for one scenario to finish before starting the next. When the run starts every collected
scenario is submitted to one event loop on a background thread that shares a single HTTP
client and judge connection, and each pytest item then waits for its own result in order.
The output, `-x` and `-k` behave exactly as before; the difference is that a run of fifty
scenarios at ten runs each is bounded by the two concurrency caps, not by fifty sequential
event loops. `run.concurrency` caps requests in flight to your endpoint and
`judge.concurrency` caps requests to the judge, so a slow judge does not hold up sampling
and a fragile staging endpoint can be throttled without starving the judge. Stopping with
`-x` cancels the scenarios that were still in flight. `.juried/verdicts.jsonl` is appended
under a file lock, so `pytest-xdist` workers do not interleave lines.

## How a scenario passes

Each scenario runs `runs` times. The judge marks each response pass or fail with a one
line reason, using a fixed prompt and the configured temperature, if any. The scenario passes
when no more than `misses` of those attempts fail, so with the defaults, `runs = 20` and
`misses = 1`, it needs 19 passes out of 20. Set `misses = 0` to require every attempt to
pass, or raise `runs` and `misses` together to tolerate the same miss rate on more evidence.

```toml
[run]
runs = 50
misses = 3    # gate needs 47/50 passes
```

juried prints what the gate needs at the top of every run
(`juried: gate needs 19/20 passes (1 miss tolerated)`), repeats it in every gate failure
and shows it in the report. `threshold`, the gate setting before 0.3, still works for this
release: juried derives `misses` from it and prints a notice naming the value to set instead.
`docs/upgrading-0.3.md` has the details.

A failing gate is a normal pytest failure that shows the passes, the misses tolerated, the
interval and the first failing transcript with the judge's reason.

Errors are kept out of the maths. An HTTP error from your endpoint is a transport error
and a judge that cannot answer (missing key, refusal, API outage) is a judge error; neither
counts as a failed run. The pass rate and interval are computed over the attempts that
reached a verdict, and the scenario is reported as incomplete, which still fails the
pytest item, with the errors listed first and the quality figures for the judged attempts
beneath them. A wobbly staging endpoint therefore shows up as transport errors, not as a
drop in quality. A misconfigured `response_path` or a header that names an unset
environment variable stops the run with one clear message instead of a traceback.
Every verdict is appended to `.juried/verdicts.jsonl` with the judge model and timestamp.

## Caching

Every run samples the feature afresh. That is the point of the tool: a scenario only shows
its flakiness if each attempt is a new request, so responses are never replayed by default.
Verdicts are cached in `.juried/cache/verdicts` by content hash, so a response the judge
has already seen, word for word, is not judged again; the cache key includes the judge
model, temperature and prompt version, and `--no-cache` bypasses it. Verdicts are not
cached at all when `votes` is above 1, since the point of votes is to re-measure agreement.

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
  unreliable on it; either way, look at the wording before trusting the verdict. With
  votes above 1 verdicts are never read from or written to the cache, because a cached
  majority would freeze the agreement figure; every run re-judges and re-measures it, and
  the header says so (`cache off (votes > 1)`).

  Votes measure stability, not correctness. Every vote comes from the same model with the
  same prompt, so agreement tells you whether the judge is consistent, not whether it is
  right. A judge that is confidently wrong agrees with itself every time, and a model run
  at temperature 0 will show agreement of 1.0 on every scenario while saying nothing about
  whether its verdicts match a human's. Votes catch a judge that wavers; only calibration,
  below, catches a judge that is wrong.
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

`juried init` writes `calibration/example.yaml` with two placeholder cases; replace them
with real responses from your feature, labelled by your team. Until a calibration report
exists under `reports/`, every run with a real judge ends with a warning that its verdicts
have not been checked against human labels. For a worked set covering the hard categories
(inverted meaning, talking to the judge, hedged guesses, wrong contact details, empty and
off topic answers) see `examples/faq-bot/calibration/`.

## What a run costs

A run costs target calls plus judge calls: each attempt sends every turn and the final
message to your endpoint, then sends the response to the judge once per vote, so twenty
runs of a two turn scenario are sixty requests to the feature and twenty to the judge.
Every run counts both sides and prints them at the end:

```
judge usage: 41,220 input + 2,860 output tokens over 130 call(s), estimated $0.11 at list prices of 2026-09
target usage: 260 requests, 84,100 input + 12,300 output tokens, estimated $0.43 at configured prices
estimated run cost: $0.54 (judge $0.11 + target $0.43)
```

The judge side comes from the token counts in each provider response, priced from a built
in table of list prices, dated in the output, which covers the current Anthropic and OpenAI
models. Set `input_price` and `output_price` under `[judge]`, in US dollars per million
tokens, to use your own figures; for a model not in the table the run reports the tokens
and says the price is unknown until you set them. Cached verdicts cost nothing and are not
counted. `juried generate` and `juried calibrate` print their own judge usage line.

The target side is always counted in requests, with the HTTP status, bytes and latency of
each recorded per attempt, but juried cannot know what your endpoint costs unless you say.
If its reply carries token counts, name them with `usage_input_path` and
`usage_output_path` under `[target]` and set `input_price` and `output_price` there; for an
endpoint that reports no tokens set `cost_per_request`, a flat figure per call. With
neither, the line says so:

```
target usage: 260 requests, cost unknown (set [target] input_price/output_price or cost_per_request)
```

The combined line appears only when both sides have a figure. All of it is in the JSON
report under `summary.usage.judge`, `summary.usage.target` and
`summary.usage.total_estimate_usd` (null when unknown), and per scenario under `usage`, and
the HTML report's spend tile shows both sides. It is an estimate: cache reads are priced as
ordinary input, the table lags price changes, and your account may have its own rates.

To see the bill before paying it, run `juried run --dry-run` (or `juried estimate`). It
collects every scenario, counts the requests each side would get, allowing for `turns` and
`votes`, prices them and exits without sending anything:

```
juried: dry run, nothing is sent
13 scenarios under scenarios, 130 attempts in all, 1 with live turns
target: 150 requests (one per turn and final message per attempt), estimated $0.14 at 400 input + 150 output tokens per call (assumed) and configured prices
judge: 130 calls (130 attempts) to claude-sonnet-5, estimated $0.30 at 400 input + 150 output tokens per call (assumed) and list prices of 2026-09
estimated run cost: $0.44 (judge $0.30 + target $0.14)
note: assumed token counts are a placeholder; a run reports the real figures and the next dry run uses its averages
```

Until a report exists the dry run assumes 400 input and 150 output tokens per call on each
side and says so; once `reports/juried-report.json` exists it uses that run's averages
instead.

## Comparing runs

One run tells you where the feature stands; two tell you which way it is moving. Keep the
JSON report from a known good run (a release, or last night's main) and compare the next
one against it:

```
juried compare reports/baseline.json reports/juried-report.json --json reports/compare.json
```

Scenarios are matched by id. The command lists, in this order, gates that were upheld and
now fail, scenarios that became incomplete through transport or judge errors, new scenarios
that fail their gate, and scenarios whose pass rate or lower bound dropped even though the
gate still holds. Improvements and added or removed scenarios follow. The exit status is 1
when there is any regression, so it works as a CI step; `--tolerance 0.05` ignores drops
smaller than five points, for noise on scenarios with few runs. `--json` writes the same
findings to a file.

## The report

After a run juried writes `reports/juried-report.html` and `reports/juried-report.json`.
The HTML is a single self contained file with no scripts. It opens with one row of totals,
states the gate rule once above the first table, then lists each acceptance criterion with
its description, a table of its scenarios showing passes over judged attempts, the passes
the gate needs and the misses tolerated, pass rate, interval, response latency and gate
result, and beneath the table each scenario's message, expectation and every failing run
with the response and the judge's reason. Criteria with no scenarios are called out so
coverage gaps are visible. The JSON file holds the same structure plus every attempt, for
anyone who wants to chart trends.

**Reading the interval.** Next to every pass rate the report shows its Wilson 95% interval,
computed over the attempts that reached a verdict. It describes how far the rate could move
on another sample and does not decide the gate: at 20 runs it is about 20 points wide, so
18 of 20 is reported as 90% with an interval of 70% to 97%, and two runs whose intervals
overlap have not been shown to differ. The JSON report also carries `threshold`, the lower
bound the gate is equivalent to, for dashboards that plotted it before 0.3.

<img src="https://raw.githubusercontent.com/jch1887/juried/main/docs/report.png" alt="juried report for the example project: totals including judge spend, two opening hours scenarios upheld at 10 of 10, and a refund scenario failed at 8 of 10 because its lower bound of 49% is below the 70% threshold" width="900">

The screenshot is the example project's hand written scenarios under the stub judge, which
is why the spend is nil, taken with juried 0.2.1, when the gate was a threshold on the
lower bound. The refund scenario passed eight of ten runs and fails its gate; in 0.3 the
same table shows the passes the gate needs (9 of 10 in the example) in place of the
threshold and lower bound columns. The warning above the tables is the coverage check: one
criterion had no scenarios in that run.

## Try it without API keys

```
cd examples/faq-bot
python server.py &       # deterministic fake FAQ bot on port 8765
juried generate          # uses the stub provider from juried.toml
JURIED_RUN_REPORT_DIR=reports/stub juried calibrate   # the stub against the 32 cases in calibration/
juried run               # 13 scenarios, one fails its gate on purpose
cp reports/juried-report.json reports/baseline.json
juried run               # sample the bot again
juried compare reports/baseline.json reports/juried-report.json   # exit 1 if the flaky scenario dropped
kill %1
```

The example's `juried.toml` sets `runs = 10` so the loop is quick; a real project should
keep the default of 20. `make example` runs the same sequence from the repository root.

The stub judge is a substring matcher: it passes any non empty response that contains every
`"quoted phrase"` in `expected` and ignores the rest of the expectation. It shows the
mechanics of runs, gates and reports, and says nothing about how an LLM judge behaves.
One hand written refund scenario fails its gate on purpose, because the fake bot drops
the 14 day detail every fourth time, which is the kind of flakiness juried exists to catch.

`juried calibrate` in the same directory runs the stub against the 32 labelled responses
under `calibration/`, with its report sent to `reports/stub/` so that it does not overwrite
the committed one. The stub agrees with 24 of the 32 labels and passes the eight it should
fail, which is the point: calibrate before you trust any judge. The same 32 cases were
checked against `claude-haiku-4-5` on 13 September 2026 and it agreed with every label; the
report is at
[examples/faq-bot/reports/juried-calibration.json](examples/faq-bot/reports/juried-calibration.json).
`docs/calibration.md` explains how to build a set of your own from real responses.

## Roadmap

Not there yet, and shaped so they can be added without changing the scenario format:

- A `Target` that drives a UI rather than an HTTP endpoint.
- Further `Provider` implementations for other judges.
- Adversarial scenario kinds, generated to attack the criterion rather than exercise it.

## Stability

From 1.0.0 onwards juried keeps these backwards compatible within a major version, and a
change to any of them is a new major version:

- `juried.toml`: every key, its type, its default and its meaning. New keys may be added;
  existing keys are not removed or repurposed.
- The scenario YAML shape: `criterion`, `scenarios`, and each scenario's `id`, `name`,
  `kind`, `message`, `expected`, `history`, `turns`, `runs`, `misses` and `tags`.
  `threshold` is deprecated and is removed in 0.4.
- The calibration YAML shape: `criterion`, `cases`, and each case's `name`, `criterion`,
  `message`, `history`, `expected`, `response`, `verdict` and `note`.
- The JSON report and the calibration report, governed by their `schema_version` field.
  Fields may be added without a bump; a field changing meaning or going away bumps it, and
  `juried compare` refuses reports of different versions.
- The CLI: the subcommands `init`, `generate`, `calibrate`, `run`, `estimate` and `compare`,
  their flags, their exit codes, and the pass through of pytest arguments from `run`.
- The pytest markers `juried`, `criterion(id)`, `happy_path`, `edge_case` and `custom`, and
  the `user_properties` written to JUnit XML: `criterion`, `passes`, `runs`, `pass_rate`,
  `interval_lower`, `interval_upper`, `misses_tolerated`, `passes_needed`, `threshold` and
  `transport_errors`.
- The `JURIED_*` environment variables: `JURIED_<SECTION>_<KEY>` overrides for every config
  key, and `JURIED_LIVE`.

Explicitly not covered, and free to change in any release: the judge and generation prompts
(their version is recorded with every verdict so a change never reuses an old one), the HTML
report layout, the pricing table and its dates, the terminal output wording, and the cache
layout under `.juried/`.

Until 1.0.0, a 0.x release may still change any of the items above. Every such change is
listed under "Breaking changes" in `CHANGELOG.md` for that release.

## Development

```
make check      # ruff, mypy and pytest
```

The same target runs in CI on every pull request. The `Live provider contract` badge at
the top of this file shows the latest result of the live workflow, which runs weekly and
on demand and exercises both the Anthropic and OpenAI clients with one judge call and one
generate call each. Every run uploads the pytest output as an artifact, so a green run can
be checked to have tested both providers rather than skipped them; in this repository a
skipped provider fails the run. See CONTRIBUTING.md for how to run it locally, and for
the development install. Changes are recorded in `CHANGELOG.md` and the release steps in
`docs/releasing.md`.

Licensed under the MIT licence.
