# Changelog

All notable changes to juried are recorded here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). Until 1.0.0 any interface may
change between minor versions; every such change is listed under "Breaking changes".

## [Unreleased]

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

[Unreleased]: https://github.com/jch1887/juried/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/jch1887/juried/compare/v0.1.1...v0.2.0
