# A2: target-side usage and --dry-run

The usage line reported judge tokens only, and the README's `$0.11` was judge spend
against a free stub. The feature under test is also an LLM, and a run makes
`runs x scenarios x (turns + 1)` calls to it. This counts and prices that side, and adds a
dry run so the bill can be read before it is paid.

## What changed

- Every request to the target is recorded per attempt (`TargetRequest`: HTTP status, bytes,
  latency, token counts when reported, and whether it failed) and summed per scenario and
  per run as a `TargetUsage`. Replayed responses send nothing and count nothing.
- `[target]` gains `usage_input_path` / `usage_output_path` (dotted paths to token counts
  in the reply, read the same way as `response_path` and validated as numbers),
  `input_price` / `output_price` (US dollars per million tokens) and `cost_per_request`.
  The validator requires paths and prices in pairs, prices to come with paths, and one of
  prices or a per request cost, each with its own message. All five are in the README
  example, `.env.example`, and `juried init`'s template.
- The pytest summary always prints a `target usage:` line in one of three forms (tokens and
  estimate; per request estimate; request count and "cost unknown" naming the keys to set)
  and an `estimated run cost:` line when both sides have a figure.
- JSON: `summary.usage` and each scenario's `usage` gain `judge`, `target` and
  `total_estimate_usd`, with the flat judge keys kept (see decisions); attempts carry
  `target_usage` and `requests`; a top level `target` entry records the URL template and
  prices. HTML: the spend tile shows the total (or "judge + ?") with both sides beneath,
  and a warning paragraph says how to price an unknown target.
- `juried run --dry-run` and `juried estimate` (`src/juried/estimate.py`): load every
  scenario, count attempts, target requests (`runs x (turns + 1)`) and judge calls
  (`attempts x votes`), price them, print the plan and exit 0. Tokens per call are 400
  input + 150 output, labelled "assumed", unless `reports/juried-report.json` exists, in
  which case its averages are used and labelled. Pytest arguments after `--dry-run` are
  reported as not narrowing the plan.
- The example's fake bot returns `usage.prompt_tokens` / `usage.completion_tokens` (word
  counts) and its `juried.toml` prices them, so `make example` shows a target figure.
- README "What a run costs" rewritten; `estimate` added to the command list and the
  Stability list; `docs/upgrading-0.3.md` gains a section.

## Decisions the brief did not cover

- No `schema_version` bump. The brief's nested `summary.usage.judge` / `target` layout
  would have moved the existing flat keys, and `compare` refuses reports of different
  versions, so the flat keys are kept as the judge's figures and `usage.judge` repeats them.
  The layout is redundant but nothing reading a 0.2 report breaks.
- A retried request inside one `send` is not counted separately: one request is recorded
  per turn per attempt, with the final status. A send that fails after its retries is
  recorded with `error: true` and the failing status, so the count matches what the
  endpoint saw at the level a bill is likely to reflect.
- A configured usage path that is missing from a reply, or that selects a non number, is a
  `TargetConfigError` and stops the scenario, as a bad `response_path` does.
- `input_price` / `output_price` without usage paths is rejected rather than silently
  pricing zero tokens; `cost_per_request` and token prices together are rejected.
- The dry run reads the last report for per call averages rather than always assuming;
  the brief allowed "when no better figure exists" and a previous run is the better figure.
- The dry run does not honour pytest selection (`-k`, paths); it plans every scenario under
  `scenarios/` and says so if arguments were given.
- The judge side of the dry run uses the judge's token guess for all calls including votes;
  votes multiply calls, not tokens.

## Checks

`make check` (ruff, mypy, pytest): 166 passed. The example project end to end under the
stub: `juried estimate` before any report planned 130 requests each side at assumed
tokens; the run printed `target usage: 130 requests, 860 input + 1,673 output tokens,
estimated $0.0092 at configured prices` and the combined line; a second `estimate` used the
report's averages (7 input + 13 output per call) and labelled them "the last report".
