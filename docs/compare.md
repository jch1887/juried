# Comparing runs

One run tells you where the feature stands; two tell you which way it is moving. Keep the
JSON report from a known good run (a release, or last night's main) and compare the next
one against it:

```
juried compare reports/baseline.json reports/juried-report.json --json reports/compare.json
```

Scenarios are matched by id. The command lists, in this order, gates that were upheld and
now fail, scenarios that became incomplete through transport or judge errors, new scenarios
that fail their gate, and scenarios whose pass rate dropped by a statistically significant
amount even though the gate still holds. Improvements and added or removed scenarios
follow. The exit status is 1 when there is any regression, so it works as a CI step, and
`--json` writes the same findings to a file.

A drop is tested, not subtracted: with 20 runs a pass rate has an interval about 20 points
wide, so 20/20 against 18/20 is the kind of difference two samples of the same feature
produce. For each scenario in both reports juried runs Fisher's exact test, one sided for a
decrease, on the passes and fails, and reports the difference with its Newcombe 95%
interval. A drop is a regression when its p-value is below `--alpha` (default 0.05) and the
rate fell by at least `--min-effect` (default 0.10, ten points); smaller or less certain
drops are listed under "drops within noise" with their p-value and interval, so they are
visible without failing the step. `--tolerance` from 0.2 is a deprecated alias for
`--min-effect`. After the table a note says the smallest drop the run counts could have
shown, for example `at 20 vs 20 runs this comparison can only detect drops of about 34
points or more`; a comparison that must catch smaller regressions needs more runs on both
sides.

Both reports should come from full sampling. A scenario that stopped early has a pass
rate that is a bound, not an estimate, because a lost one stops at the moment its
failures cross the line, and Fisher's test assumes a fixed sample; `juried compare`
refuses such a scenario on either side with a message naming it. Re-run both sides with
`juried run --no-early-stop` (or `early_stop = false` under `[run]`), or pass
`--allow-early-stopped` to compare anyway, in which case a warning is printed and the
findings on those scenarios are hints. Separately, when a scenario's attempts made differ
by more than a factor of two between the reports, a note says so, since the smaller run
bounds what the comparison can show.
