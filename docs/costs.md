# What a run costs

Counting and pricing both sides of a run, and seeing the bill before paying it.

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
early stop: expected about 122 of 160 attempts (137 target requests, 122 judge calls) from the last report's pass rates for 16 of 16 scenarios; expected run cost $0.41 (judge $0.28 + target $0.13)
note: assumed token counts are a placeholder; a run reports the real figures and the next dry run uses its averages
```

Until a report exists the dry run assumes 400 input and 150 output tokens per call on each
side and says so; once `reports/juried-report.json` exists it uses that run's averages
instead.

The plan prices every attempt; the `early stop` line is what early stopping is expected to
leave of it. For each scenario the last report's pass rate feeds an exact dynamic
programme over the (passes, fails) states a scenario can be in before its gate is
decided, which gives the expected attempts; a scenario the last report does not know is
planned in full, and without a report the line says the saving is unknown. The figure is a
floor, since attempts in flight when the gate is decided still finish, and it is small for
a healthy suite: at 20 runs and 1 miss a scenario that passes 95% of the time is expected
to make about 18 attempts, while one that fails half the time is decided in about four.
`juried run --dry-run --no-early-stop` plans the full sample only.
