# Upgrading to 0.3

0.3 changes how a scenario's gate is decided. This page lists what changed, what it means
for an existing `juried.toml`, and the exact edit to make. Later 0.3 changes are added here
as they land.

## The gate is a count of misses, not a threshold

Before 0.3 a scenario passed when the lower bound of the Wilson 95% interval on its pass
rate met `threshold`. With 20 runs and the default threshold of 0.7 that meant 19 passes
out of 20, which the README needed a table to explain. From 0.3 a scenario passes when no
more than `misses` of its attempts fail. The interval is still computed and reported, but it
describes the rate rather than deciding the gate.

| before 0.3                    | from 0.3                    |
|-------------------------------|-----------------------------|
| `runs = 20`, `threshold = 0.7` | `runs = 20`, `misses = 1`   |
| `runs = 10`, `threshold = 0.7` | `runs = 10`, `misses = 0`   |
| `runs = 50`, `threshold = 0.7` | `runs = 50`, `misses = 8`   |
| `runs = 20`, `threshold = 0.8` | `runs = 20`, `misses = 0`   |

Apply this diff to `juried.toml`, using the value the run header prints for your own
numbers:

```diff
 [run]
 runs = 20
-threshold = 0.7
+misses = 1
```

and the same to any scenario that sets `threshold` in its YAML:

```diff
     runs: 20
-    threshold: 0.8
+    misses: 0
```

`threshold` still works in 0.3. When it is set and `misses` is not, juried derives `misses`
as the largest count whose lower bound still met the threshold and prints one notice at the
top of the run:

```
juried: threshold is deprecated and is removed in 0.4: threshold 0.7 with 20 runs tolerates 1 miss, so set misses = 1 instead
```

When both are set and disagree, the config is rejected with both numbers, and so is a
threshold no run count could meet (such as 0.9 with 10 runs), which used to be a warning
that every scenario would fail. `threshold` is removed in 0.4.

What changes without any edit:

- A scenario that sets `runs` alone used to inherit the threshold rule at its own count, so
  50 runs tolerated 8 misses. It now inherits `misses`, so 50 runs tolerate 1 unless the
  scenario sets its own `misses`. While the deprecated `threshold` is still in your config
  the old derivation applies, so nothing moves until you make the edit above.
- `juried run --threshold` still works but `--misses` is the flag to use. Either flag
  replaces the file's gate outright.

## Reports and JUnit

- JSON: scenario entries and `defaults` gain `misses`. `required_passes` is always an
  integer and `threshold` is always present, derived when the key is not set, so nothing
  reading those fields breaks. `schema_version` stays at 1.
- JUnit `user_properties` gain `misses_tolerated` and `passes_needed`; `threshold` is still
  written.
- `juried compare` accepts reports from 0.2 and 0.3 alike.
