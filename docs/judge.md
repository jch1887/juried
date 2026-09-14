# The judge

How verdicts are made, checked against human labels, and corrected for the judge's
error rate. The README's "Trusting the judge" and "What the judge's mistakes cost you"
summarise this page.

## How a verdict is made

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

The set grows from real output. After every run juried queues the responses worth a
human's eye in `.juried/label-queue.jsonl`, each tagged with why: the judge's votes split,
the scenario finished within one miss of its gate, a deterministic check and the judge
disagreed, the judge's reason hedged (a fixed word list in `label.py`), or a random 2% of
the rest so the set is not only hard cases; up to `queue_size` (default 50) under
`[label]`, newest first, deduplicated by content. `juried label` shows each one in the
terminal with the judge's verdict last so you decide first, takes `p`, `f`, `s` (skip),
`n` (note) or `q`, and appends every label to `calibration/from-runs/<criterion>.yaml`
in the usual shape with a `source` naming the run; `juried calibrate` picks that
directory up and says how many cases came from hand labelling and how many from runs.
`juried label --html` writes `reports/label-queue.html`, a page with no scripts, and a
`label-queue.csv` beside it, for a QA lead to mark up away from the terminal, and
`juried label --import` reads either back. The loop is: run, label what the run flags,
calibrate, and the corrected figure tightens as the judge's error rates are measured on
more of your own responses. The run's summary says how many responses are waiting and how
big the calibration set is per criterion.

`juried init` writes `calibration/example.yaml` with two placeholder cases; replace them
with real responses from your feature, labelled by your team. Until a calibration report
exists under `reports/`, every run with a real judge ends with a warning that its verdicts
have not been checked against human labels. For a worked set covering the hard categories
(inverted meaning, talking to the judge, hedged guesses, wrong contact details, empty and
off topic answers) see `examples/faq-bot/calibration/`.

## What the judge's mistakes cost you

A judge that passes 6% of bad responses and fails 3% of good ones does not just add noise;
it moves the number. juried measures both rates with `juried calibrate` and, when a
calibration report for the configured judge exists under `reports/`, corrects every
scenario's pass rate for them:

```
corrected = (observed + specificity − 1) / (sensitivity + specificity − 1)
```

Sensitivity is the share of human-labelled passes the judge passed, specificity the share
of labelled fails it failed. They are taken per criterion when it has at least
`min_calibration_cases` (default 10) labelled cases under `[judge]`, otherwise from the
whole set. The interval is a bootstrap of 2,000 resamples over the calibration cases and
the attempts together, from a fixed seed recorded in the report, so it carries the
uncertainty in the judge as well as in the run. Each scenario then reports:

```
18/20 judged pass; corrected 84% (72–93%), judge false pass 6%, false fail 3%
```

When sensitivity plus specificity minus one is below 0.5 the judge is too weak to
correct, and the report says so with the calibration accuracy instead of a figure. The gate
stays on the observed passes in 0.3; set `gate_on = "corrected"` under `[run]` to gate on
the corrected interval's lower bound against the rate `misses` implies, which the release
after 0.3 will make the default. Without a matching calibration report the run says there
is no corrected rate and how to get one.
