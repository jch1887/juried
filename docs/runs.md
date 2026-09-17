# Runs

What happens during `juried run`: gates and errors, concurrency, caching, the report and
the example walkthrough. What it costs, and the dry run that prices it first, are in
[costs.md](costs.md).

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

## Early stopping

A scenario stops as soon as its gate is decided. It is lost once the failed attempts
exceed `misses`, and won once the passes reach the count the gate needs, `runs - misses`;
at that point the attempts not yet sent are skipped, whatever is in flight finishes and
counts, and the scenario is reported as `stopped after 7 of 20`. The saving is
asymmetric. A passing scenario saves at most `misses` attempts, because it has to collect
`runs - misses` passes before it is decided, so at the default 20 runs and 1 miss a green
suite saves one attempt per scenario. A failing scenario can stop at attempt `misses + 1`,
so a bad deploy is caught after two attempts rather than twenty. The value is fast failure
on a bad deploy, not a cheaper green run. With concurrency above one a few more attempts
than the minimum are made, since the attempts already in flight when the decision lands
are allowed to finish; none is ever cancelled mid request.

Every scenario records `attempts_planned` and `attempts_made`, the latter counting every
attempt whose request completed, transport errors included, and `early_stopped`. The
pass rate, interval and corrected rate are computed over the judged attempts as before.
The header says `early stop on (a scenario ends once its gate is decided)` and the
summary says `early stop saved 14 of 400 planned attempts`; the report shows
"stopped after 7 of 20" against each stopped scenario in the HTML, the JSON and the
JUnit `user_properties`.

A stopped scenario's gate verdict is sound, but its rate is not an estimate. A lost
scenario stops at the moment its failures cross the line, so its rate is a bound on the
feature's quality, biased downwards; a won one stops the moment its passes suffice, biased
upwards. Anything that reads the rate as a measurement should use full sampling:
`juried compare` refuses stopped scenarios unless told otherwise, the calibration set
should be built from full runs, and `gate_on = "corrected"` turns early stopping off
because its interval needs every attempt, which the header says
(`early stop off (gate_on = corrected needs full sampling)`). Set `early_stop = false`
under `[run]`, or pass `juried run --no-early-stop`, to run every planned attempt.

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
`-x` cancels the scenarios that were still in flight, and a scenario whose gate is decided
skips the attempts it had not started (see "Early stopping"). `.juried/verdicts.jsonl` is appended
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
