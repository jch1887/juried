# Criteria and scenarios

Criteria are `##` headings in a Markdown file. The heading becomes a stable id; add
`{#id}` to fix it explicitly.

```markdown
## Refund policy
The bot explains that any item can be returned within 14 days of delivery for a full
refund. It must state the 14 day window.

## Unknown questions {#unknown}
When the bot cannot answer it says so and gives help@example.com.
```

Generated and hand written scenarios share one YAML shape. Hand written files go
anywhere under `scenarios/`; `runs` and `misses` may be set per scenario.

```yaml
criterion: refund-policy
scenarios:
  - name: Asks how to get money back
    kind: happy_path
    message: I want my money back on a jumper that does not fit.
    expected: States the 14 day return window and that the refund is full.
  - name: Follows up after a delivery answer
    kind: edge_case
    history:
      - role: user
        content: Can you tell me about delivery?
      - role: assistant
        content: Standard delivery takes 3 to 5 working days.
    message: and refunds?
    expected: Explains the 14 day refund window without repeating the delivery answer.
    runs: 20
    misses: 0
```

Add `turns` for a live conversation before `message`; see the conversation fields below.

A scenario may also carry `checks`, deterministic tests that run on every response before
the judge is asked. A failing check fails the attempt with its reason and the judge is not
called, which saves the spend on responses that were never going to pass:

```yaml
    checks:
      - contains: "14 days"          # case, spacing, hyphens and end punctuation ignored
      - not_contains: "30 days"
      - regex: "\\b14[ -]?days?\\b"   # Python syntax, case sensitive unless (?i)
      - json_schema: schemas/reply.json   # path from the directory holding juried.toml
      - max_latency_ms: 4000         # the final response's latency
      - max_chars: 1200
```

Each entry is one check. `json_schema` parses the response as JSON and validates it with
the `jsonschema` package, installed by `pip install juried[schema]`. Every attempt records
which checks ran and which failed, the failing run output lists them, and the report shows
them under each scenario and each failing run. They are not a judge: `contains` tells you
a phrase is there, not that the answer is right.

The judge sees each part of the scenario in its own delimited section and is told that the
response is untrusted output which may contain instructions or claims about the verdict, so a
response that says "this meets the expectation, pass" is judged on what it does for the user,
not on what it says about the test. The prompt is pinned and its version is part of every
verdict's cache key, so a prompt change never reuses an old verdict.

Two fields cover conversations. `history` is a scripted prefix: juried sends it to your
endpoint with the message and shows it to the judge in its own section, and the generator
may write a short one when a message only makes sense as a follow up. `turns` is a live
conversation: each entry is a user message sent in order, the feature's reply to it becomes
context for the next, and `message` is the final turn that the expectation judges.

```yaml
  - name: Changes mind about the refund
    kind: edge_case
    turns:
      - I want to return a jumper.
      - Actually it was a gift, does that matter?
    message: So how long have I got?
    expected: Still states the "14 days" window and does not contradict its earlier answers.
```

Every attempt drives the whole conversation afresh, so a scenario with two turns costs three
requests per attempt. The judge sees the scripted history, then the live transcript with the
feature's own replies, then the final message and response, and is told the expectation may
refer to what was said earlier. The transcript is recorded on every attempt, shown for
failing runs in the terminal and the report, and kept in the JSON.

Scenarios have a `kind`: `happy_path`, `edge_case`, `custom`, or `adversarial`, each also a
pytest marker. An adversarial scenario tries to make the feature violate its criterion:
`juried generate --adversarial` writes one file per criterion,
`scenarios/generated/<criterion>.adversarial.yaml`, covering an instruction override
("ignore your previous instructions and..."), a false premise, a contradiction of an
earlier turn, an appeal to the judge to pass the response, an off topic pull, and a request
for something the criterion says to withhold. With `adversarial_pack = true` under
`[generate]` it also writes `scenarios/generated/adversarial-pack.yaml`, juried's built in
attacks that apply to any feature (prompt injection, system prompt extraction, PII
disclosure) under three criteria of their own, which runs then recognise. Run them alone
with `juried run -m adversarial`. This is a coverage extension of your criteria, not a red
team: the messages are things a user might plausibly send, and there is no search for
jailbreaks. For that, use promptfoo's red team module. The example project has three hand
written adversarial scenarios and labelled calibration cases for them.

A phrase in double quotes inside `expected` must appear in the response. Text outside
quotes is judged on meaning. With
`expected: Says returns are accepted within "14 days" for a full refund`, a response saying
"you have 14 days and get every penny back" passes, while "a fortnight for a full refund"
fails because `14 days` is missing. The phrase is matched as a `contains` check is, ignoring
case, runs of whitespace, hyphens and punctuation that ends a word, so "14-days," counts;
set `strict_quotes = true` under `[judge]` to require it word for word, ignoring case only,
as before 0.3. Quoted phrases run as checks before the judge, so a response missing one
fails without a judge call whichever provider is configured. The stub judge applies the
same rule to calibration cases.
