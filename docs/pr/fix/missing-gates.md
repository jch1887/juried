# A1: gate on tolerated misses

`threshold = 0.7` read as a pass rate but meant "Wilson lower bound at least 0.7", which at
20 runs required 19 of 20 and needed a warning and a nine row table to explain. This
replaces it with `misses`, the number of failed attempts a scenario may have and still
pass. The gate rule is `passes >= runs - misses`; the Wilson interval is still computed and
shown, as a description of the rate rather than the gate.

## What changed

- `[run] misses` (default 1) and a per scenario `misses`. `threshold` is deprecated for this
  release: set alone it derives `misses` from the old table logic and the run header prints
  a notice naming the value to set; set alongside a disagreeing `misses`, or at a value no
  run count could meet, config validation fails with both numbers. `misses >= runs` is also
  rejected, since that gate could never fail. Removed in 0.4.
- `juried run --misses N` and `JURIED_RUN_MISSES`. A command line `--misses` or `--threshold`
  replaces the file's gate rather than being checked against it.
- Terminal output drops the word threshold: `gate needs 19/20 passes (1 miss tolerated)` in
  the header, `19/20 (needs 19, interval 0.75 to 0.99)` per scenario, and a gate failure
  that states the gate, the misses on the judged attempts and the interval.
- JSON report: `misses` on scenario entries and `defaults`; `required_passes` always an
  integer; `threshold` always populated (configured value while the deprecated key is set,
  otherwise the lower bound the gate is equivalent to). No field changed meaning, so the
  schema version stays at 1.
- JUnit `user_properties`: `misses_tolerated` and `passes_needed` added, `threshold` kept.
- HTML report: a "Gate needs" column and one "Interval" column replace the threshold, lower
  bound and upper bound columns.
- `juried compare` reads what a gate needed from `misses`, `required_passes` or
  `threshold`, so 0.2 reports still compare, and writes `passes_needed` in `--json`.
- README: the threshold paragraph and table are gone; "How a scenario passes" is three
  sentences and a two line example, and the interval is explained once under "The report".
  `docs/upgrading-0.3.md` is new. `.env.example`, `juried init` and the example project use
  `misses`.

## Decisions the brief did not cover

- The gate on an incomplete scenario (one with transport or judge errors) is judged on the
  attempts that reached a verdict, as the threshold rule was: `judged - passes <= misses`.
  For a complete scenario this is exactly `passes >= runs - misses`.
- A scenario that sets `runs` but not `misses` inherits `misses` from `[run]`, not a value
  scaled to its run count. While the deprecated `threshold` is in the config the old
  derivation still applies at the scenario's own count, so nothing moves until the config
  is edited. The upgrading notes say so.
- The derived `threshold` in reports is the Wilson lower bound of the smallest passing
  score (19 of 20 gives 0.7639), rounded to four places, unless the deprecated key is set,
  in which case the configured value is kept so dashboards see no jump.
- The README's screenshot still shows the 0.2.1 table; its caption now says so. It needs
  regenerating from the example project once the branch is merged.

## Checks

`make check` (ruff, mypy, pytest): 159 passed. The example project was run end to end
under the stub (generate, calibrate, run, run, compare): 12 gates upheld, the refund
scenario fails at 8/10 with 9 needed, and compare flags it as dropped between the two runs.
