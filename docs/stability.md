# Stability

From 1.0.0 onwards juried keeps these backwards compatible within a major version, and a
change to any of them is a new major version:

- `juried.toml`: every key, its type, its default and its meaning. New keys may be added;
  existing keys are not removed or repurposed.
- The scenario YAML shape: `criterion`, `scenarios`, and each scenario's `id`, `name`,
  `kind`, `message`, `expected`, `history`, `turns`, `checks`, `runs`, `misses` and `tags`.
  `threshold` is deprecated and is removed in 0.4.
- The calibration YAML shape: `criterion`, `cases`, and each case's `name`, `criterion`,
  `message`, `history`, `expected`, `response`, `verdict` and `note`.
- The JSON report and the calibration report, governed by their `schema_version` field.
  Fields may be added without a bump; a field changing meaning or going away bumps it, and
  `juried compare` refuses reports of different versions.
- The CLI: the subcommands `init`, `generate`, `calibrate`, `run`, `estimate` and `compare`,
  their flags, their exit codes, and the pass through of pytest arguments from `run`.
- The pytest markers `juried`, `criterion(id)`, `happy_path`, `edge_case`, `custom` and
  `adversarial`, and
  the `user_properties` written to JUnit XML: `criterion`, `passes`, `runs`, `pass_rate`,
  `interval_lower`, `interval_upper`, `misses_tolerated`, `passes_needed`, `threshold` and
  `transport_errors`.
- The `JURIED_*` environment variables: `JURIED_<SECTION>_<KEY>` overrides for every config
  key, and `JURIED_LIVE`.

Explicitly not covered, and free to change in any release: the judge and generation prompts
(their version is recorded with every verdict so a change never reuses an old one), the HTML
report layout, the pricing table and its dates, the terminal output wording, and the cache
layout under `.juried/`.

Until 1.0.0, a 0.x release may still change any of the items above. Every such change is
listed under "Breaking changes" in `CHANGELOG.md` for that release.
