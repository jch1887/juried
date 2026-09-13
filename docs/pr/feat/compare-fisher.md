# A3: compare is a statistical test, not a subtraction

With 20 runs a pass rate has roughly a 20 point interval, so flagging any drop, with
`--tolerance 0.05` as the noise floor, turned CI red on sampling variance. `juried compare`
now tests each drop and fails the step only on one the run counts can actually support.

## What changed

- `stats.py` gains `fisher_decrease_p` (one sided Fisher's exact test as a hypergeometric
  tail in integer arithmetic with `math.comb`), `newcombe_difference` (Newcombe's hybrid
  score interval for a difference in proportions, built from the two Wilson intervals),
  `normal_quantile` (Acklam's approximation) and `detectable_drop` (the smallest drop a
  one sided two proportion test would detect at alpha with 80% power, by the normal
  approximation, found by bisection). Pure Python; no new dependencies.
- `compare.py` classifies in the brief's order: gate lost, newly incomplete, new failing,
  then a significant drop (`p < alpha` and point drop `>= min_effect`). Other drops are
  "drop within noise", listed under their own heading with the p-value and difference
  interval and not counted as regressions. A rise is "improved" only when the same test,
  turned round, is significant; otherwise it is "unchanged". The lower bound no longer
  takes part.
- `--alpha` (0.05) and `--min-effect` (0.10). `--tolerance` is a deprecated alias that
  prints a notice to stderr; giving both with different values is an error. Both flags are
  range checked.
- After the findings the command prints the power note, computed from the smallest
  judged count on each side across paired scenarios, at the given alpha, from a 90% pass
  rate. At 20 vs 20 runs that is 34 points.
- `--json` gains `schema_version` (1, the first the comparison file has carried),
  `alpha`, `min_effect`, `detectable_drop`, `within_noise`, and per change `p_value`,
  `diff` (new minus old), `diff_interval` and `significant`; `tolerance` is gone.
- README "Comparing runs" explains the test, the flags and the note; the example's final
  `compare` comment now says the flaky scenario's wobble is noise. `docs/upgrading-0.3.md`
  gains a section on the flag rename and what will stop failing.

## Decisions the brief did not cover

- The comparison JSON had no `schema_version` before, so "bump" means adding one at 1
  and dropping `tolerance` from the file.
- Improvements are held to the same test rather than any rise counting, so the
  "improvements" list carries the same weight as "regressions". Rises within noise are
  reported as "unchanged" rather than under a fourth heading.
- The power note's baseline is a fixed 90% pass rate, stated in the note, rather than the
  median of the old report, so the figure is comparable between comparisons and easy to
  reason about. The judged counts used are the smallest on each side, which bound what
  the comparison as a whole can show.
- Newcombe's interval and Fisher's test are computed on judged attempts, as the rates
  are; incomplete scenarios still classify by their errors first.

## Checks

`make check` (ruff, mypy, pytest): 175 passed. The example project, run twice under the
stub and compared: the flaky refund scenario's 8/10 to 7/10 is listed as a drop within
noise (diff -0.10, interval -0.44 to +0.26, p 0.500), exit 0, and the note says 10 vs 10
runs can only detect drops of about 50 points.
