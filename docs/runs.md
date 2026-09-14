# Runs

What happens during `juried run`: gates and errors, concurrency, caching, cost, the
dry run, the report and the example walkthrough.

## Gates and errors


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

Under `pytest-xdist` every worker is its own process with its own event loop, so the caps
would otherwise apply per worker: `concurrency = 4` with `-n 4` would be sixteen requests
in flight. juried reads the worker count and divides both caps by it, never below one each,
so the total stays what the file says; the header prints the per worker figure
(`concurrency 4 target / 4 judge (1 target / 1 judge per worker, 4 xdist workers, scope
global)`). Set `concurrency_scope = "worker"` under `[run]` to give every worker the full
caps instead. Scenarios are only split across workers, never a scenario's attempts, so a
single scenario's runs stay on one worker.

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
16 scenarios under scenarios, 160 attempts in all, 1 with live turns
target: 180 requests (one per turn and final message per attempt), estimated $0.17 at 400 input + 150 output tokens per call (assumed) and configured prices
judge: 160 calls (160 attempts) to claude-sonnet-5, estimated $0.37 at 400 input + 150 output tokens per call (assumed) and list prices of 2026-09
estimated run cost: $0.54 (judge $0.37 + target $0.17)
note: assumed token counts are a placeholder; a run reports the real figures and the next dry run uses its averages
```

Until a report exists the dry run assumes 400 input and 150 output tokens per call on each
side and says so; once `reports/juried-report.json` exists it uses that run's averages
instead.

## The report

**Reading the interval.** Next to every pass rate the report shows its Wilson 95% interval,
computed over the attempts that reached a verdict. It describes how far the rate could move
on another sample and does not decide the gate: at 20 runs it is about 20 points wide, so
18 of 20 is reported as 90% with an interval of 70% to 97%, and two runs whose intervals
overlap have not been shown to differ. The JSON report also carries `threshold`, the lower
bound the gate is equivalent to, for dashboards that plotted it before 0.3.

The screenshot in the README is the example project's hand written scenarios under the stub judge, which
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
JURIED_RUN_REPORT_DIR=reports/stub juried calibrate   # the stub against the 38 cases in calibration/
juried run               # 16 scenarios, one fails its gate on purpose
cp reports/juried-report.json reports/baseline.json
juried run               # sample the bot again
juried compare reports/baseline.json reports/juried-report.json   # exit 1 only on a significant drop; 8/10 to 7/10 is noise
kill %1
```

The example's `juried.toml` sets `runs = 10` so the loop is quick; a real project should
keep the default of 20. `make example` runs the same sequence from the repository root.

The stub judge is a substring matcher: it passes any non empty response that contains every
`"quoted phrase"` in `expected` and ignores the rest of the expectation. It shows the
mechanics of runs, gates and reports, and says nothing about how an LLM judge behaves.
One hand written refund scenario fails its gate on purpose, because the fake bot drops
the 14 day detail every fourth time, which is the kind of flakiness juried exists to catch.

`juried calibrate` in the same directory runs the stub against the 38 labelled responses
under `calibration/`, with its report sent to `reports/stub/` so that it does not overwrite
the committed one. The stub agrees with 28 of the 38 labels and passes the ten it should
fail, which is the point: calibrate before you trust any judge. The first 32 of those cases were
checked against `claude-haiku-4-5` on 13 September 2026 and it agreed with every label; the
report is at
[examples/faq-bot/reports/juried-calibration.json](../examples/faq-bot/reports/juried-calibration.json).
[docs/calibration.md](calibration.md) explains how to build a set of your own from real responses.
