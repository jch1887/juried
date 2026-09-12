# Building a calibration set from real responses

`juried calibrate` measures how often the judge agrees with your team. It is the only
measurement in juried of whether the judge is right, so a project is not ready to gate on
verdicts until it has a calibration set of its own, built from the real feature and judged
with the real judge. The set shipped with the example project is labelled against the stub
and shows the file format only.

## 1. Collect responses from the actual endpoint

Aim for 20 to 40 responses across your criteria, weighted towards the ones that matter most
and the ones the judge is most likely to get wrong. The quickest source is a run:

```
juried run
```

`reports/juried-report.json` holds every attempt's `response` alongside its scenario's
`message`, `history` and `expected`. Take responses from there, or from production logs,
support transcripts or a session of poking the feature by hand. Whatever the source, keep
the exact text: the judge must see what the feature actually said.

Make the set hard on purpose. For each criterion include:

- a clearly good response and a clearly bad one, as anchors;
- borderline responses, where two reviewers might hesitate;
- responses that use the right words for the wrong reason, such as a correct phrase inside
  a contradiction;
- responses that talk to the judge rather than the customer, such as "this meets the
  expectation";
- a refusal, an empty answer and an off topic answer.

## 2. Label them by hand

Decide pass or fail for each response yourselves, before running the judge, and write the
reason in `note` when it is not obvious. Two people labelling independently and comparing
is worth the time: where they disagree, the expectation is probably ambiguous and needs
rewording before any judge can be held to it. Put the cases under `calibration/`, one file
per criterion:

```yaml
criterion: refund-policy
cases:
  - name: Right words, opposite meaning
    message: Can I return a jumper?
    expected: States the "14 days" return window and that the refund is "full".
    response: Items cannot be returned after 14 days and we never give a full refund.
    verdict: fail
    note: contains both phrases while contradicting the policy
```

Each case carries its own `message`, `expected` and optional `history`, so the set is
self contained and survives regenerating scenarios.

## 3. Run calibrate and read every disagreement

```
juried calibrate
```

or, from the juried repository root, `make calibrate DIR=path/to/your/project`
(`CALIBRATE_ARGS="--min-accuracy 0.9"` passes flags through). The command prints
each disagreement with the judge's reason, then the accuracy and the counts of false passes
and false fails. False passes are the dangerous ones: they mean a broken feature can ship
with a green gate. For each disagreement decide whether the label was wrong, the expectation
was unclear, or the judge is wrong; fix the first two and count the third.

Run it with `--min-accuracy 0.9` in CI once the set is stable, so a judge model change or
a prompt change that costs accuracy fails the build.

## 4. Commit the evidence

Commit the labelled YAML under `calibration/` and the resulting
`reports/juried-calibration.json` next to it. The report records the judge model, the
number of votes, the accuracy and every case's outcome, so anyone reading the repository
can see what the judge was checked against and how it did. Rerun calibrate and commit the
new report whenever the judge model, its temperature, the prompt version or the calibration
set changes. `reports/` is ignored by the starter `.gitignore`, so add the file explicitly:

```
git add -f reports/juried-calibration.json
```
