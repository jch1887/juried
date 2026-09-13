# Changelog

All notable changes to juried are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0.0 any interface may
change between minor versions; every such change is listed under "Breaking changes".

## [Unreleased]

### Breaking changes

- `juried compare` tests a drop instead of subtracting it. For each scenario in both
  reports it runs Fisher's exact test, one sided for a decrease, on the passes and fails,
  and a drop is a regression only when its p-value is below `--alpha` (default 0.05) and
  the pass rate fell by at least `--min-effect` (default 0.10). Smaller or less certain
  drops are listed under "drops within noise" with the p-value and the Newcombe 95%
  interval for the difference, and do not fail the step; a drop in the interval's lower
  bound alone is no longer a regression, and a rise is "improved" only on the same test
  turned round. `--tolerance` is a deprecated alias for `--min-effect`, removed in 0.4.
  The comparison JSON gains `schema_version` (1), `alpha`, `min_effect`,
  `detectable_drop` and `within_noise` at the top level and `p_value`, `diff`,
  `diff_interval` and `significant` per change; `tolerance` is gone from it.
- The gate is a count of tolerated misses, not a threshold on the interval. `[run] misses`
  (default 1) and a per scenario `misses` say how many attempts may fail, and a scenario
  passes when `passes >= runs - misses`. The Wilson interval is still computed and shown but
  no longer decides. With the default 20 runs the requirement is unchanged at 19 of 20; a
  scenario that set `runs` without a threshold now tolerates one miss at any count rather
  than the count the threshold table gave it. `threshold` is deprecated and is removed in
  0.4: set without `misses`, it derives `misses` (the largest count whose lower bound still
  met it) and the run header prints a notice naming the value to set; set alongside a
  disagreeing `misses`, or at a value no run count could meet, it is a config error rather
  than a warning. A `misses` at or above `runs` is also an error, since such a gate could
  never fail.
- Terminal output no longer mentions a threshold. The header prints
  `gate needs 19/20 passes (1 miss tolerated)`, the pytest summary shows
  `19/20 (needs 19, interval 0.75 to 0.99)`, and a gate failure states the gate, the misses
  on the judged attempts and the interval.
- The JSON report's scenario entries and `defaults` carry `misses`. `required_passes` is
  always an integer, and `threshold` is always populated: the configured value while the
  deprecated key is set, otherwise the interval lower bound the gate is equivalent to. No
  field changed meaning, so `schema_version` stays at 1.
- The HTML report's scenario table shows "Gate needs" and one "Interval" column in place of
  the threshold, lower bound and upper bound columns.

### Added

- `[judge] api_key_env` and `[generate] api_key_env` name the environment variable holding
  the provider's key when it is not `ANTHROPIC_API_KEY` or `OPENAI_API_KEY`, so a second
  account, a proxy or a local endpoint can have its own; generation inherits the judge's
  while the provider is the same, as it does `base_url`. With `provider = "openai"` and
  `base_url` any OpenAI compatible endpoint (Ollama, vLLM, LM Studio, OpenRouter, Azure
  OpenAI with its path) serves as judge and generator: against a custom `base_url` the
  client sends `max_tokens`, which those servers know, rather than `max_completion_tokens`.
  The live provider workflow sends one judge call to a local Ollama when the runner has
  one and prints a notice, not a failure, when it does not.
- `juried compare` prints, after the findings, the smallest drop the two runs' sizes could
  have detected at the given alpha with 80% power from a 90% pass rate, so a green
  comparison at 20 runs is not mistaken for proof that nothing moved.
- The target side of a run is counted and priced. Every request to the endpoint is recorded
  per attempt with its HTTP status, bytes and latency, and summed per scenario and per run.
  New `[target]` keys: `usage_input_path` and `usage_output_path`, dotted paths to token
  counts in the reply; `input_price` and `output_price` in US dollars per million tokens;
  and `cost_per_request`, a flat price per call for a target that reports no tokens. The
  summary always prints a `target usage:` line, with tokens and an estimate, a per request
  estimate, or the request count and "cost unknown" with the keys to set, and an
  `estimated run cost:` line whenever both sides have a figure.
- `juried run --dry-run`, also `juried estimate`: collects every scenario, counts the
  requests each side would get allowing for `turns` and `votes`, prices them from the
  configured target prices and the judge price table, prints the plan and exits 0 without
  sending anything. It assumes 400 input and 150 output tokens per call and says so, or
  uses the averages from the last `reports/juried-report.json` when one exists.
- The JSON report's `summary.usage` and each scenario's `usage` gain `judge`, `target` and
  `total_estimate_usd` (null when either side is unknown); the existing flat keys are the
  judge's figures and are kept. Attempts carry `target_usage` and a `requests` list, and
  the report has a top level `target` entry with the URL template and prices. The HTML
  spend tile shows both sides, and the report warns when the target's cost is unknown.
- `juried run --misses N` and `JURIED_RUN_MISSES`, overriding `[run] misses`. A command
  line `--misses` or `--threshold` replaces the file's gate outright, so it cannot disagree
  with it.
- JUnit `user_properties` `misses_tolerated` and `passes_needed`; `threshold` is still
  written, derived as above, so existing dashboards keep working.
- `juried compare` reads the passes a gate needed from `misses`, `required_passes` or
  `threshold`, whichever the report carries, and writes it as `passes_needed` in `--json`.
- `docs/upgrading-0.3.md`, the migration notes for 0.3.

### Changed

- The README's "What a run costs" starts from the fact that a run costs target calls plus
  judge calls, shows both usage lines, and shows `--dry-run`. The example project's fake
  bot now reports token counts and its `juried.toml` prices them, so the example shows a
  target figure rather than only the stub judge's nil spend.
- The README's "How a scenario passes" no longer needs a warning and a lookup table to
  explain the gate; the interval is described once under the report section.

## [0.2.1] - 2026-09-13

### Added

- A calibration set of 32 labelled responses for the example project, eight per criterion,
  and its report against `claude-haiku-4-5`, which agreed with every label. The example's
  stub calibrate run now writes to `reports/stub/` so it does not overwrite that report,
  and `docs/calibration.md` describes the set as the pattern to copy.
- Check, Live provider contract and PyPI version badges at the top of the README.

### Changed

- The live provider workflow fails when either provider was skipped for want of a key,
  naming the missing secret, so a green run means both clients were exercised.
- The HTML report's totals sit on one row of tiles, or two even rows when the replay and
  split verdict tiles apply, and the gate rule is stated once above the first table rather
  than under every table. The README's screenshot is refreshed to match.
- `juried generate` no longer prints a usage line under the stub provider, which spends
  nothing.

### Fixed

- An empty `judge.model` or `generate.model`, whether from `juried.toml` or an empty
  `JURIED_JUDGE_MODEL`, now stops the run with "judge.model must not be empty" instead of
  reaching the provider and coming back as an HTTP 400. The live tests treat an empty
  model override as unset, which is how the workflow exports it.
- The sdist no longer carries the README logo and the report screenshot, which the README
  references by URL; it is under 200 KB rather than over 1 MB.

## [0.2.0] - 2026-09-12

### Breaking changes

- The default `runs` is 20 rather than 10. At the default threshold of 0.7 a scenario now
  needs 19 of 20 passes, so one miss is tolerated; 10 runs needed all 10. Each scenario
  costs twice as many requests unless `runs` is set explicitly (#26).
- Transport and judge errors are no longer part of the pass rate. The rate and the Wilson
  interval are computed over attempts that reached a verdict, and any error makes the
  scenario "incomplete", which still fails the pytest item but is reported separately from
  a quality failure (#21).
- Responses are no longer replayed from the cache by default. Every run samples the feature
  afresh; replay is opt in with `cache_responses = true` or `juried run --cache-responses`,
  and a run that replayed anything says so loudly (#18).
- The JSON report gained fields. Scenario entries carry `judged`, `judge_errors`,
  `quality_met`, `status`, `required_passes`, `responses_from_cache`, `judge_agreement`,
  `split_verdicts`, `usage` and `turns`; attempts carry `judge_error`, `votes`,
  `agreement`, `usage` and `transcript`; failures carry `agreement`, `votes` and
  `transcript`; `summary` carries `incomplete`, `judge_errors`, `responses_from_cache`,
  `split_verdicts` and `usage`; `judge` carries `votes` and `prices_usd_per_million`;
  `defaults` carries `required_passes`. `gates_failed` no longer counts incomplete
  scenarios (#17, #18, #20, #21, and the cost and multi turn changes).
- Verdicts cached by 0.1.x are not reused. The judge prompt changed (prompt version 2 to 5)
  and the cache key now includes the vote index and the live transcript, so `.juried/`
  from an earlier release is ignored rather than trusted (#19, #20, #28).
- `judge` provider implementations must accept a `transcript` argument on `judge` and
  return usage from `complete_json` (#28 and the cost change).

### Added

- `juried calibrate`: judges responses your team has labelled by hand under `calibration/`
  and reports accuracy, false passes and false fails, with `--min-accuracy` as a CI gate.
  `juried init` writes a starter `calibration/example.yaml`, and a run with a real judge
  and no calibration report ends with a warning (#20, #25).
- `juried compare old.json new.json`: lists gates lost, scenarios newly incomplete, new
  failing scenarios and drops in pass rate or lower bound, and exits 1 on any regression.
  `--tolerance` ignores small drops and `--json` writes the findings to a file.
- `[judge] votes`: judge each response an odd number of times and take the majority; the
  report shows split verdicts and the pytest summary flags them. Verdicts are not cached
  while votes are above 1, so agreement is re-measured on every run (#20, #27).
- Token usage and estimated spend. Every provider response's usage is recorded on its
  verdict and summed per attempt, scenario and run; the pytest summary, the JSON and HTML
  reports, `generate` and `calibrate` all report tokens and an estimate at dated list
  prices, overridable with `[judge] input_price` and `output_price`.
- Live multi turn scenarios through a `turns` field: each turn is answered by the feature
  and its reply becomes context for the next; the transcript is recorded per attempt,
  shown for failing runs and given to the judge in its own section (#28).
- Generated scenarios may carry a short `history` for follow up messages (#24).
- `[judge] concurrency`, a separate cap on judge requests in flight (#22).
- `[generate] temperature` and `base_url`, so generation no longer borrows the judge's
  temperature (#24).
- `${NAME}` expands in `[target]` `url` and `body` as well as `headers`, and an unset
  variable stops the run before any request (#21, #24).
- The gate requirement is stated everywhere: the pytest header prints
  `gate needs 19/20 passes at threshold 0.70 (1 miss tolerated)`, every gate failure
  repeats it, and the report shows "needs N / M" under the threshold (#17).
- A `Check` workflow runs `make check` on Python 3.11, 3.12 and 3.13 for every pull
  request, and a weekly `Live provider contract` workflow sends one judge and one generate
  request per provider to the real APIs (#23).
- Python 3.11 is supported (#23).

### Changed

- All scenarios in a session run together on one background event loop with a shared
  client and provider, bounded by `run.concurrency` and `judge.concurrency`, instead of
  one event loop per pytest item. Output order, `-x` and `-k` are unchanged (#22).
- The judge prompt wraps every section in tags, treats the response as untrusted output
  whose instructions and claims about the verdict carry no weight, and asks its question
  after the response (#19).
- `{{message}}` and `{{history}}` follow one rule: a value that is exactly a placeholder
  keeps its type, and inside longer text each becomes text, the history as JSON (#24).
- The README says plainly that the threshold is a floor on the interval's lower bound and
  not a pass rate, with a table of runs, threshold, passes needed and misses tolerated; that
  votes measure judge stability rather than correctness; that the stub judge is a substring
  matcher; and what multi turn support does and does not do. The roadmap moved from
  CONTRIBUTING.md to the README (#17, #20, #24, and the votes note).

### Fixed

- A `TargetConfigError` on one attempt, such as a bad `response_path`, stops the scenario
  with one clear message instead of a traceback and cancels its sibling attempts (#21).
- A judge failure on one attempt is recorded as a judge error rather than crashing the
  scenario and discarding the other attempts (#21).
- `verdicts.jsonl` is appended under a file lock so `pytest-xdist` workers cannot interleave
  lines (#22).
- The always true `field.annotation` check in `apply_env_overrides` (#24).
- The README's claim that nothing but keys is ever read from disk (#24).
- Concurrency tests no longer assert wall clock time, which failed on slow CI runners (#29).

[Unreleased]: https://github.com/jch1887/juried/compare/v0.2.1...HEAD
[0.2.1]: https://github.com/jch1887/juried/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/jch1887/juried/compare/v0.1.1...v0.2.0
